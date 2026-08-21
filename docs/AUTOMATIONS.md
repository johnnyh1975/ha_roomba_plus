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
a new HA-side one? That's `calendar.{name}_schedule`, which appears
automatically. On Prime robots you can create, edit and delete entries
there and they are written back to the robot; on Classic it is read-only.
This recipe is for building something the app itself can't express, like
room-specific timing.)*

**Naming rooms in a calendar entry.** Room names are matched by text
across the event's **summary, description and location** — write the
room wherever suits you. Editing a recurring entry has to be done as
"all events": a robot schedule is one rule, so a single occurrence has
nothing separate to change.

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

## Zone cleaning on demand (Prime)

The iRobot app no longer lets you save a zone as a favourite — only
rooms. `roomba_plus.clean_zone` is how you send the robot to a zone from
Home Assistant instead.

```yaml
# A dashboard button that cleans one zone
script:
  clean_the_kitchen_zone:
    alias: Clean kitchen zone
    sequence:
      - action: roomba_plus.clean_zone
        target:
          entity_id: vacuum.house_bot
        data:
          zone_name: ["Clean Kitchen"]
```

Zones can be given by name, exactly as they appear on the map, or by
numeric id:

```yaml
      - action: roomba_plus.clean_zone
        target:
          entity_id: vacuum.house_bot
        data:
          zone_id: ["100", "101"]
```

Provide **either** `zone_name` or `zone_id`, not both. Several zones in
one call are cleaned in one mission.

A name that does not exist on the map raises an error listing the names
that do — rather than skipping it, because a partial clean looks like a
successful one.

**Where do the names come from?** The map bundle's `cleanZones` layer,
which is what the map card shows. If a zone has no name there, use its
id.

## Prime robots: what changed for automations

Until v4.0.0a31 a Prime robot had **no `phase` sensor**, and every device trigger that watches
robot state — "starts cleaning", "finishes cleaning", "docks", "gets stuck" — reads exactly that
sensor. So those triggers did not appear in the automation editor at all for Prime users, with no
error and no explanation. They work now.

Two attributes are worth knowing about, both on the mission event sensor:

```yaml
# Rooms the last run left undone, with the mission that skipped them.
# A room still waiting looks different from one picked up on the next pass.
{{ state_attr('sensor.robot_mission_event', 'unfinished_rooms') }}
# -> {"Kitchen": 61}
```

`readiness` says why a robot will not start. It matters more on Prime than the name suggests: **a
Prime robot refuses a start silently**, and this is the only place that says why. An unmapped reason
shows as its code rather than a blank, so it can be quoted in a report.

### Asking before you send (a34)

Four faults stop a mission before it starts, and three of them leave one half
of the robot working. `binary_sensor.{name}_start_blocked` says which:

```yaml
# Mop when the pad plate is fitted, vacuum when it is not, and say why
# when neither is possible.
- choose:
    - conditions: >
        {{ 'mop' in state_attr('binary_sensor.robot_start_blocked',
                               'available_modes') | default([], true) }}
      sequence:
        - action: roomba_plus.clean_room
          target: {entity_id: vacuum.robot}
          data: {room_name: Kitchen}
    - conditions: "{{ is_state('binary_sensor.robot_start_blocked', 'on') }}"
      sequence:
        - action: notify.mobile_app
          data:
            message: >
              {{ state_attr('binary_sensor.robot_start_blocked',
                            'blocked_reason') }}
```

`available_modes` is empty when nothing works — a robot off the floor — so the
first branch falls through to the notification rather than sending a command
that will be refused.

There is a device trigger for it too, *Start blocked by a fault*, which fires
when a block appears rather than being polled.

**Nothing is prevented.** The command still goes out if you send it; this only
lets you ask first.

### Cleaning a room the way it is set up

The Rooms Map image carries a `room_preferences` attribute: the settings each
room already has in the iRobot app.

```yaml
# Clean the kitchen with its own suction level rather than a fixed one.
- variables:
    prefs: >
      {{ state_attr('image.robot_rooms_map', 'room_preferences') | default({}, true) }}
- action: roomba_plus.clean_room
  target: {entity_id: vacuum.robot}
  data:
    room_name: Kitchen
    two_pass: "{{ prefs.get('7', {}).get('two_pass', false) }}"
```

Keys are room ids; each value carries whatever that room reports — `profile`,
`suction_level`, `two_pass`, `carpet_boost`, `scrub`. **An absent key means the
robot did not report it, which is not the same as a zero**, so read with a
default rather than assuming.

The point is to honour what somebody configured in the app instead of
overriding it: *"clean the kitchen the way I set it up"* rather than *"clean
the kitchen on deep because the automation says so"*.

**Not confirmed on any robot yet.** The settings come from
`operating_mode_defaults`, and a room only carries them under the mode it last
ran in. If your `room_preferences` is empty, that is worth reporting — a
diagnostics download now says which of the two reasons applies.

### Quiet hours (a34)

```yaml
# Do not start a clean during a scheduled quiet-hours window.
condition:
  - condition: state
    entity_id: binary_sensor.robot_in_quiet_hours
    state: "off"
```

`switch.{name}_do_not_disturb` turns DND on and off directly, and
`roomba_plus.set_quiet_hours` writes the window itself.

**Do the enforcing here rather than trusting the robot.** A Prime robot has
been observed cleaning inside its own quiet-hours window — the setting is
accepted and reads back, and whether it is honoured is a separate question no
field report has answered yes. The condition above is the reliable half.

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
