[Roomba+](../../README.md) › [Documentation](../README.md) › Events and device triggers

# Events and device triggers

What Roomba+ fires on the event bus, which event entities mirror it, and the device triggers in the automation editor.

**Applies to:** all robots; where an event or field differs by generation, the row says so. How to use them for notifications: [Notifications and blueprints](../guides/notifications.md).

Roomba+ fires events on Home Assistant's event bus, for automations with an *Event* trigger; mirrors the main ones as [event entities](#event-entities), which every user's dashboard can see; and offers a set of [device triggers](#device-triggers) in the automation editor, without YAML. Every event carries `entry_id` and `name`, so an automation can tell which robot.

## Event bus

**Applies to:** all robots; the row says where an event or field differs

| Event | Fires when | Payload |
|---|---|---|
| `roomba_plus_mission_completed` | A mission ends (any result) | `entry_id`, `name`, `rooms_cleaned` (a count), `area_sqft`, `stuck_count`, `result`, `mission_id`, `missions_added`, and `is_anomalous`, `anomaly_reason`, `recommended_action`, `robot_lifted`, always present (`null`/`false` for an ordinary mission), so a notification gets the reason without calling an action. On Prime `stuck_count` is `null`, and a sync that imports several missions at once (the first after setup imports the history) fires once, for the newest, with `missions_added` saying how many |
| `roomba_plus_mission_started` *(new in 4.6)* | Roomba+ recognises a mission's start, both generations; not for one already running at setup | `entry_id`, `name`, `initiator`, `scope`, `rooms`, `areas`, `mission_number`, `started_at`; see [When a mission starts](../guides/scripts-and-assist.md#when-a-mission-starts-and-which-room-it-is-in) |
| `roomba_plus_mission_reassessed` | Classic with the iRobot login: the cloud's numbers for a mission of the last 24 h arrive (minutes after it ended) and make it unusual for another reason than at its end -- typically `dirt_spike`, which a Classic robot's own report cannot show. Once per mission | `entry_id`, `name`, `mission_id`, `ended_at`, `is_anomalous` (`true`), `anomaly_reason`, `recommended_action`, `anomaly_reason_before` (the verdict without the cloud's numbers, `null` if ordinary) |
| `roomba_plus_room_completed` | A room finished. Classic: room tracking moved on; Prime: the timeline moved to the next region | `entry_id`, `name`, `room_name`, `duration_sec` (Classic: what room tracking measured, Prime: from the room event's start in the timeline to the next event's, `null` when the timeline gave no start for the room); Classic `room_idx`, Prime `room_id`, `mission_id`; `area_id`, `area_name` (None without an area) |
| `roomba_plus_health_change` | `sensor.*_integration_health` crosses a band (healthy/degraded/critical) | `entry_id`, `name`, `score`, `previous_score`, `band`, `previous_band` |
| `roomba_plus_map_retrain_started` / `_completed` | Cloud detects a Smart Map change and syncs | `entry_id`, `name`, `pmap_id` |
| `roomba_plus_maintenance_reset` | A part or care task marked done: button, list or action | `entry_id`, `name`, `component`, `hours` (`null` for calendar-based resets) |
| `roomba_plus_stuck` | Classic: the robot went silent during a mission. **Prime:** the robot reports `phase: stuck`, once per time it gets stuck (not for a robot already stuck when Home Assistant started) | `entry_id`, `name`, `last_room`, `phase`, `stuck_count`, `minutes_stuck`, `last_known_position` (if pose data available: `x_mm`/`y_mm` in millimetres from the dock and `map_x_mm`/`map_y_mm` in the maps' frame, as on the tracker; `x`/`y` are the firmware's raw centimetres). On Prime `stuck_count`, `minutes_stuck` and `last_known_position` are `null`: the robot reports none of them |
| `roomba_plus_mission_result` *(new in 4.6)* | A mission's result is stored, both generations. Not for a Prime sync that imports several missions at once | `entry_id`, `name`, `mission_id`, `type` (`completed`, `completed_with_skips`, `cancelled`, `stuck_abandoned`, `error`), `result` (the stored result), `skipped_rooms`, `missed_rooms` (asked for, not skipped, not cleaned — only where the rooms were observed during the run), `requested_rooms`, `error_code`. On Prime the rooms asked for come from the mission history's command and the cleaned ones from its timeline, named as on the map; a Prime robot reports no skipped rooms |
| `roomba_plus_command_not_delivered` *(new in 4.6)* | A command that should start a mission was sent from a closed cycle and the robot did not start within 20 s (Classic) or 60 s (Prime). Watched: every start Roomba+ sends — the start, `clean_room`, `clean_zone`, `vacuum.clean_area`, `vacuum.send_command` with `start`, `run_favorite`, `smart_start` (also a start the blocking sensors let through), `auto_clean_dirty_rooms`, demand cleaning, the zone, Smart Map zone and favourite buttons and *Repeat last mission*; `command` says which (`start`, `clean_room`, `clean_zone`, `clean_area`, `send_command`, `run_favorite`, `smart_start`, `auto_clean_dirty_rooms`, `demand_clean`, `zone_button`, `favorite`, `repeat_last_mission`). A start from the robot's own schedule or the iRobot app is not Roomba+'s and is not watched. A start into an open mission is not watched: whether the robot took it cannot be told from the state | `entry_id`, `name`, `command`, `phase`, `not_ready`, `error`, `waited_sec`, `resent` (`true` when Roomba+ had already sent it a second time, see below) |
| `roomba_plus_state_frozen` *(new in 4.6)* | The robot's state has said `run` for at least ten minutes while its battery climbed three points or more. A robot cannot clean and charge at once: the state has stopped following the robot, and commands are likely to be ignored until it is power-cycled. Once per episode | `entry_id`, `name`, `phase`, `error`, `minutes_in_run`, `battery_from`, `battery_to` |
| `roomba_plus_all_away` · `roomba_plus_person_detected_during_clean` | [Presence-aware scheduling](../guides/schedules.md#presence-aware-scheduling) | — |
| `roomba_plus_start_blocked` · `roomba_plus_start_timeout` | The [blocking sensors](../guides/start-conditions.md): a start held back (behaviour *abort*), a waiting start given up (behaviour *queue*) | `entry_id`, `name`, `blocking_entities`, `rooms` (`start_blocked`, `null` for the whole home) and `waited_sec` (`start_timeout`) |

`roomba_plus_mission_completed`, `roomba_plus_maintenance_reset`, and `roomba_plus_stuck` also produce rich, searchable **Logbook** entries automatically — no setup needed.

**Sent once more.** When a command was not delivered and the robot is idle —
no `notReady` reason, no error, the state not frozen — Roomba+ sends it a second time
and waits again. Only once, never mid-mission, never into a frozen state. The event
fires either way, with `resent` saying which. A command reported as not delivered is
watched five minutes longer: a mission that begins then is not announced again, but the
diagnostics record it (`command_watches`, outcome `started_late`, with the time it took),
next to `started` with the time for every start that came in time. That is how the
60 s for Prime will be checked against real robots. The option *Send an ignored command once
more* (on by default, under *Settings*) turns this off.

## Event entities

**Applies to:** all robots

The moments a dashboard most wants to react to are also **event entities**:

| Entity | Mirrors | Robots |
|---|---|---|
| `event.{name}_mission_completed` | `roomba_plus_mission_completed` | both generations |
| `event.{name}_room_completed` | `roomba_plus_room_completed` | both generations |
| `event.{name}_stuck` | `roomba_plus_stuck` | both generations |
| `event.{name}_mission_result` | `roomba_plus_mission_result`; the event type is the result type | both generations |
| `event.{name}_command` | `roomba_plus_command_not_delivered` (type `not_delivered`), `roomba_plus_state_frozen` (type `state_frozen`), `roomba_plus_start_blocked` (type `start_blocked`) and `roomba_plus_start_timeout` (type `start_timeout`) | both generations |

Each changes state when its bus event fires for that robot, with the event's payload as its attributes (`event_type` plus the fields above). The bus events fire as well.

**Why both.** Home Assistant lets only administrators subscribe to an integration's own bus events from the frontend. A dashboard card opened by any other user never heard a mission end; an entity state reaches every user who can see the robot.

## Device triggers

**Applies to:** all robots; only the triggers the robot can fire are offered

In the automation editor: **Add trigger → Device**, then the robot.

`cleaning_started` · `cleaning_finished` · `stuck` · `bin_full` · `start_blocked` · `docked` · `error` · `room_completed` · `maintenance_due` · `health_score_drop` · `map_retrain_started` · `map_retrain_completed` · `firmware_updated`

**Only the triggers that can fire are offered.** Each one watches an entity, and a
robot without that entity is not offered the trigger: a Prime robot has no `bin_full`
sensor, a robot without blocking sensors no `start_blocked`. The map retrain triggers
are Classic only. `start_blocked` follows the blocking-sensor sensor on Classic and the
robot's own blocking codes on Prime.

`health_score_drop` fires only when the health band gets worse (healthy → degraded, say), not on every change of the score and not when it improves.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
