[Roomba+](../README.md) › [Documentation](README.md) › Automations & Dashboards

# Roomba+ — Automations & Dashboards

Copy-paste automation recipes and a starter dashboard. For the full list of
available events, device triggers, and services, see the
**[Events and device triggers →](reference/events.md)**.

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
a new HA-side one? That's `calendar.{name}_schedule` on Classic robots and
`calendar.{name}_prime_schedule` on Prime ones, which appears
automatically. On both you can create, edit and delete entries there, and
they are written back to the robot.
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
      entity_id: binary_sensor.roomba_map_saving
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

<a id="daily-catch-up-clean-for-overdue-rooms-v330"></a>

### Daily catch-up clean for overdue rooms

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

<a id="only-start-if-it-is-done-in-time-460"></a>

### Only start if it is done in time

*New in 4.6.*

`plan_clean` answers what a clean of the rooms you name would take if it
started now, without starting it. With `end_by` the answer says whether it
`fits`. Travel between rooms and recharges are not in the room estimates;
`plan.past_error` says how far recent missions' estimates were off. A room
without an estimate leaves `plan.total_sec` empty and `plan.fits` with it, so
the script below then does not start.

```yaml
script:
  roomba_kitchen_before_five:
    sequence:
      - action: roomba_plus.plan_clean
        data:
          entity_id: vacuum.roomba
          room_name: [Kitchen, Hall]
          end_by: "{{ today_at('17:00') }}"
        response_variable: plan
      - if: "{{ plan.fits }}"
        then:
          - action: roomba_plus.clean_room
            target:
              entity_id: vacuum.roomba
            data:
              room_name: [Kitchen, Hall]
```

<a id="clean-the-rooms-a-mission-missed-460"></a>

### Clean the rooms a mission missed

*New in 4.6.*

`roomba_plus_mission_result` fires when a mission's result is stored. Its
`type` is `completed_with_skips` when rooms were skipped or not reached, and
`retry_missed_rooms` cleans exactly those. Prime robots, and Classic robots
with a Smart Map and an iRobot login.

```yaml
automation:
  alias: "Roomba — finish the rooms it missed"
  trigger:
    - platform: event
      event_type: roomba_plus_mission_result
      event_data:
        type: completed_with_skips
  condition:
    - condition: time
      after: "09:00:00"
      before: "20:00:00"
  action:
    - action: roomba_plus.retry_missed_rooms
      data:
        entity_id: vacuum.roomba
```

With more than one robot, add the robot's `entry_id` to `event_data` so each
automation reacts to its own robot only.

<a id="tell-me-when-a-start-did-not-arrive-460"></a>

### Tell me when a start did not arrive

*New in 4.6.*

Sometimes a robot accepts a start and stays on the dock.
`roomba_plus_command_not_delivered` fires when the robot has not started
20 s (Classic) or 60 s (Prime) after a start Roomba+ sent. `resent` says
whether Roomba+ already sent it a second time. The same moment shows on
`event.<robot>_command`, which users without admin rights can see too.

```yaml
automation:
  alias: "Roomba — start did not arrive"
  trigger:
    - platform: event
      event_type: roomba_plus_command_not_delivered
  action:
    - action: notify.mobile_app
      data:
        message: >-
          {{ trigger.event.data.name }} did not start
          ({{ trigger.event.data.command }}{{ ', sent twice' if trigger.event.data.resent }}).
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
{{ state_attr('sensor.robot_prime_mission_event', 'unfinished_rooms') }}
# -> {"Kitchen": 61}
```

`readiness` says why a robot will not start. It matters more on Prime than the name suggests: **a
Prime robot refuses a start silently**, and this is the only place that says why. An unmapped reason
shows as its code rather than a blank, so it can be quoted in a report.

<a id="asking-before-you-send-a34"></a>

### Asking before you send

Four faults stop a mission before it starts, and three of them leave one half
of the robot working. `binary_sensor.{name}_prime_start_blocked` says which:

```yaml
# Mop when the pad plate is fitted, vacuum when it is not, and say why
# when neither is possible.
- choose:
    - conditions: >
        {{ 'mop' in state_attr('binary_sensor.robot_prime_start_blocked',
                               'available_modes') | default([], true) }}
      sequence:
        - action: roomba_plus.clean_room
          target: {entity_id: vacuum.robot}
          data: {room_name: Kitchen}
    - conditions: "{{ is_state('binary_sensor.robot_prime_start_blocked', 'on') }}"
      sequence:
        - action: notify.mobile_app
          data:
            message: >
              {{ state_attr('binary_sensor.robot_prime_start_blocked',
                            'blocked_reason') }}
```

`available_modes` is empty when nothing works — a robot off the floor — so the
first branch falls through to the notification rather than sending a command
that will be refused.

There is a device trigger for it too, *Start blocked by a fault*, which fires
when a block appears rather than being polled.

**Since 4.4.2 a start that the fault rules out is refused** (mopping
without the plate, vacuuming with it fitted, anything off the floor), with
the reason in the error. Docking, stopping and pausing always go out.

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

<a id="quiet-hours-a34"></a>

### Quiet hours

```yaml
# Do not start a clean during a scheduled quiet-hours window.
condition:
  - condition: state
    entity_id: binary_sensor.robot_prime_quiet_hours
    state: "off"
```

`switch.{name}_prime_quiet_hours_active` (shown as *Do not disturb*) turns
DND on and off directly, and
`roomba_plus.set_quiet_hours` writes the window itself.

**Do the enforcing here rather than trusting the robot.** A Prime robot has
been observed cleaning inside its own quiet-hours window — the setting is
accepted and reads back, and whether it is honoured is a separate question no
field report has answered yes. The condition above is the reliable half.

## Dashboard example

A minimal dashboard combining the map, vacuum card, key sensors, and the
maintenance to-do list. Built from Home Assistant's own cards, it works
without anything else installed. For room selection, the rooms map, the
mission history and maintenance with one-tap resets in one card, the
[Roomba+ card](https://github.com/johnnyh1975/ha_roomba_plus_card) is the
fuller option.

Two entities below depend on your setup:
- **`todo.roomba_maintenance`** is on for a robot added from 4.5.0. For an
  older one, switch it on under Configure → Maintenance → *Show maintenance
  as a to-do list*, or drop the last card.
- **The map is `image.roomba_map` on Classic robots** (an installation from
  an older version may call it `image.roomba_cleaning_map`) and
  `image.roomba_prime_cleaning_map` on Prime ones.

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.roomba_map
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

  # Robots added before 4.5.0: Configure → Maintenance → Settings → Show maintenance as a to-do list
  - type: todo-list
    entity: todo.roomba_maintenance
```

---

<a id="blueprints-are-already-there-460"></a>

## Blueprints are already there

*New in 4.6.*

From 4.6.0 every blueprint below is copied to Settings → Automations &
Scenes → Blueprints when Home Assistant starts, under `roomba_plus/`. Create
an automation from it there; the import step is only needed before 4.6.0.

- A file you already have is never overwritten.
- A blueprint you delete stays deleted: each one is copied once.
- One you imported through its link before is not copied a second time.
- A newer version of a blueprint does not replace your copy. The release notes
  say when one changed; importing it from its link again updates it.

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
5. For **Maintenance due**, pick this robot's `binary_sensor.*_maintenance_due` entity. For one notification per part, with the care guide and a Done button, use the [maintenance blueprint](#maintenance-450) instead.
6. For **Battery capacity critical**, pick this robot's `sensor.*_estimated_battery_eol` entity and, optionally, adjust the warning buffer (default: 14 days before the estimate hits zero). This sensor is self-calibrated per robot — it learns your robot's own degradation rate rather than using a fixed capacity percentage, so the same day-count buffer means something similar regardless of your robot's age or usage pattern. It also needs some cleaning history before it reports a value at all; that's expected, not a bug.

One blueprint import covers all your robots — repeat step 1 to create one automation instance per robot, each pointing at that robot's own entities.

<a id="maintenance-450"></a>

## Maintenance

A notification when a part or care task is due or overdue, with the care
guide one tap away and buttons on the phone: **Replaced** for a part,
**Cleaned** for a care task, **Only cleaned** where a part can also be
cleaned (its counter stays), **Remind me later** (a day by default) and
**How to do it**. Android shows three buttons, the guide button last;
tapping the notification opens the guide anyway. A button marks it done
in Roomba+, on a Prime robot at iRobot. Notifications wait for your
hours (08:00 to 21:00 by default; equal times mean any time) -- a restart
in between drops the waiting one, and the maintenance list still has it. An error that points to a care task arrives the same
way while the robot reports it (Configure → Maintenance → *Guide when an
error points to a care task*: maintenance list, the default).

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fjohnnyh1975%2Fha_roomba_plus%2Fmain%2Fblueprints%2Fautomation%2Froomba_plus_maintenance.yaml)

```text
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/roomba_plus_maintenance.yaml
```

Pick the robot's `event.*_maintenance` and its vacuum entity. Without a
notification action you get a persistent notification in Home Assistant
with the guide link. For the phone, with the buttons:

```yaml
action: notify.mobile_app_your_phone
data:
  title: "{{ notification_title }}"
  message: "{{ notification_message }}"
  data:
    url: "{{ notification_url }}"          # iOS: tap opens the guide
    clickAction: "{{ notification_url }}"  # Android
    actions: "{{ notification_actions }}"
```

For an entry an error brought, `{{ notification_help_url }}` is iRobot's
article for that error (empty otherwise); the persistent notification
links it below the guide.

The buttons work after a restart too: they carry the robot and the entry.
The words ("is due", "Done", ...) are inputs, so the message can be in
your language; the part's name comes in Home Assistant's language already
("Filter reinigen"). One automation per robot.

<a id="robot-went-silent-v400b4"></a>

## Robot went silent

Notifies you when the robot stops reporting — **the failure that looks
like nothing is wrong.**

A robot that goes quiet does not clear its entities. Battery keeps
showing its last percentage, phase keeps showing its last phase, and
none of it looks stale. One field tester's robot sat undiscovered for
nine days that way: off its dock, dock unpowered, battery falling, while
every entity showed a plausible value from the moment it went silent.

Triggers on the status sensor reaching `no_contact`, which the
integration sets after an hour with no message. There is no event for
this — silence is the absence of messages, so nothing fires; the
integration turns that absence into a state, and the blueprint watches
it.

Survives a restart: when no message has ever arrived, silence is
measured from when the config entry came up.

```text
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/robot_went_silent.yaml
```

The extra wait (default 15 minutes, on top of the integration's hour)
covers a robot carried to another room or a router restart — gaps that
resolve themselves. Set it to zero to be told immediately.

<a id="exception-notifications-rewritten-in-450"></a>

## Exception notifications

The opposite of the curated set: **nothing after a normal mission**, and
a notification only when something is actually wrong:

- a mission that ended in an error or stuck (cancelled, or never started
  because of blocking sensors, if you choose them);
- the same error again and again (code, room and what to do);
- the robot getting stuck in the same place at the same time;
- anomalous missions in a row;
- missions cancelled again and again (off by default);
- scheduled cleans skipped in a row -- with the robot's *Consecutive
  clean skips* sensor (disabled by default; enable it first).

Roomba+ raises each of these once per occurrence, not once per mission.
Events of other robots are ignored (matched on the config entry, not the
name).

```text
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/roomba_plus_exceptions.yaml
```

Pick the robot's vacuum entity and a notification action (the default
is a persistent notification); `{{ notification_title }}` and
`{{ notification_message }}` are available in it.

**Before 4.5.0 this blueprint did not work**: it listened for a
`repairs` event, issues and an attribute Roomba+ no longer had, and with
the skips sensor left empty Home Assistant did not load it. An
automation made from the old version has to be made again: re-import the
blueprint, then open the automation and pick the robot and the action.

<a id="three-more-blueprints-v343"></a>

## Three more blueprints

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

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
