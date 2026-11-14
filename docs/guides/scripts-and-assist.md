[Roomba+](../../README.md) › [Documentation](../README.md) › Scripts, Assist and the mission record

# Scripts, Assist and the mission record

Ask Roomba+ about rooms, missions and plans from a script, react to a mission's start, and read the history from outside Home Assistant.

**Assist.** Roomba+ ships no voice sentences of its own: a custom integration has no supported way to. Write a script that uses the actions below, expose it to Assist, and Assist runs it when asked ("how long would the kitchen take?").

## Ask from a script: rooms, missions, plans

*New in 4.6.* **Applies to:** `get_missions` and `get_mission` all robots; `get_rooms` and `plan_clean` Smart Map robots with the account and Prime

Four response actions answer from what Roomba+ already holds; none of them
asks the iRobot cloud, so a call is cheap and works with the account offline.
Call them with `response_variable:` in a script.

- **`get_rooms`**: `rooms`, each with `id`, `name`, `map_id`, `type` (the
  cloud's region type on Classic, None on Prime), `area_id`, `area_name`,
  `estimate_sec` and `estimate_quality`.
- **`get_missions`**: `missions`, newest first, each with when, `result` and
  `type` (as `event.<robot>_mission_result`), `initiator`, `scope`, the rooms
  requested, cleaned, skipped and not reached by name, `area_sqft`,
  `battery_used_pct` and `estimated_duration_min`.
- **`get_mission`**: one mission (`mission_id`, else the latest) with all of
  the above, the battery fields and `room_learning`.
- **`plan_clean`**: the rooms in the order given (`room_name`, a name or a
  list), each with its estimate and quality, `total_sec`, `start`, `end`, and
  with `end_by` whether it `fits`. Without rooms, the robot's mean mission
  length. Travel and recharges are not in the room estimates; `past_error`
  (`missions`, `median_error_pct`) says how far recent missions' estimates
  were off, which is that gap, measured. A room without an estimate leaves
  `total_sec` empty rather than a total that silently leaves it out.

```yaml
- action: roomba_plus.plan_clean
  data:
    entity_id: vacuum.robbie
    room_name: [Kitchen, Hall]
    end_by: "{{ today_at('17:00') }}"
  response_variable: plan
- if: "{{ plan.fits }}"
  then:
    - action: roomba_plus.clean_room
      target: {entity_id: vacuum.robbie}
      data: {room_name: [Kitchen, Hall]}
```

**Why a room taught nothing** (`room_learning`): each room of the mission
with `seconds`, `accepted` and `reason`: `passage` (reached into, not
cleaned), `not_finished`, `too_short` (under 30 s), `too_long` (over 3 h),
`skipped` (by command), or the mission's own `stuck`, `error`, `cancelled`,
`paused`, which keep every room of it out. The learned figures go through
the same rule, so this is what was learned. `room_learning_source` is
`timeline` (the cloud's room events: Classic with an account, every Prime
mission) or `room_tracking` (Classic without an account, which takes every
room it measures). Missions stored before 4.6 have the timeline form where
their timeline is kept, nothing otherwise.

**Estimate quality** (`estimate_quality`): `runs` (measurements in the
room's mean), `spread_sec` (their standard deviation) and `rating`: `high`
from five runs within 15 % of the mean, `medium` from three within 30 %,
`low` otherwise, `none` without a measurement of our own. A figure learned
before 4.6 keeps its mean and count and starts its spread with the next run.

**Battery per mission**: `battery_start_pct`, `battery_end_pct`,
`battery_used_pct`, `battery_used_pct_per_m2` and `battery_recharged` on the
stored mission and on `sensor.<robot>_last_mission`. The robots report a
percentage, not energy, so the use is the drop in points. A mission that
recharged on the way has no figure (`battery_recharged: true`): its end
level says nothing about what it used. A mission that began before Home
Assistant started has no start level. On Prime the figures exist for
missions Home Assistant saw run; they are attached when the history sync
stores the mission.

## When a mission starts, and which room it is in

*New in 4.6.* **Applies to:** all robots that clean by room; the start event on all robots

- **`roomba_plus_mission_started`** fires once per mission, mirrored by
  `event.<robot>_mission_started`: `initiator`, `scope` (`rooms`,
  `whole_home`), `rooms`, `areas` (the HA area of each room, or None),
  `mission_number`, `started_at`. Classic reads who and where from the
  mission; Prime from the last command, and only when it is under five
  minutes old: a schedule start issues no command, so an older one would
  describe some other start. Not for a mission already running when Home
  Assistant started.
- **`roomba_plus_room_completed`** carries `area_id` and `area_name`, None
  for a room without an area.
- **`sensor.<robot>_current_room`** and **`sensor.<robot>_estimated_end_time`**,
  created with mission progress: the room as a state (attributes
  `current_room_source`, `room_progress_observed`, `next_room`, `area_id`,
  `area_name`, `room_states`) and the expected end, to the minute.
  `room_states` lists each room of the run as `pending`, `operating`,
  `skipped` or `completed`, Matter's words, from the same resolution as the
  room; where the room is an estimate, so are they.

The area of a room is the vacuum's segment mapping first and the area name
second, one rule for these events, the sensors and the device tracker.

## When each area was last vacuumed and mopped

*New in 4.6.* **Applies to:** all robots whose rooms are mapped to areas

`sensor.<robot>_area_last_vacuumed_<area id>` and `_area_last_mopped_<area id>`, off by default,
one pair for each HA area the robot's rooms map to. **The value is the
household's**: the newest mission of that kind among all loaded Roomba+
robots, with `robot` and `room` saying which. A kitchen the Roomba vacuums
and the Braava mops has both times on either robot's pair. Vacuum or mop is
the mission's cleaning mode; a vacuum-and-mop mission counts for both, and a
robot that reports no mode is a vacuum, a Braava a mop. An area mapped later
gets its pair at the robot's next mission end.

## Which robots each action works on

**Applies to:** per action, in the table

Every field of every action, and which return data: [Actions](../reference/actions.md). Most act on one robot: target its vacuum entity, or give `entity_id`.

| Action | Robots | What it is for |
|---|---|---|
| `roomba_plus.clean_room` | Smart Map, Prime | Clean rooms by name, in an order, with settings per room ([guide](clean-rooms.md)) |
| `roomba_plus.clean_zone` | Smart Map, Prime | Clean saved zones by name or id |
| `vacuum.clean_area` | Smart Map with the account, Prime; HA 2026.3 | Clean by Home Assistant area ([guide](clean-rooms.md#clean-by-home-assistant-area)) |
| `roomba_plus.smart_start` | all | Start, held back by the blocking sensors ([guide](start-conditions.md)) |
| `roomba_plus.run_favorite` | with the account | Run a favourite saved in the iRobot app; it carries its own map |
| `roomba_plus.clean_sequence` | all | Start robot B when robot A finishes |
| `roomba_plus.clean_overdue_rooms` | Smart Map with the account, Prime | Clean every room that is due, worst first ([guide](schedules.md#rooms-that-are-due)) |
| `roomba_plus.auto_clean_dirty_rooms` | Smart Map with the account, Prime | Clean the dirty rooms ([guide](schedules.md#demand-cleaning)) |
| `roomba_plus.retry_missed_rooms` | Smart Map with the account, Prime | Clean the rooms the latest mission (or `mission_id`) skipped or did not reach, in its order. "Not reached" is known only where the rooms were seen during the run |
| `roomba_plus.advance_room` | Smart Map with the account | Move mission progress on when it sticks on a finished room |
| `roomba_plus.get_rooms` | Smart Map with the account, Prime | Returns the rooms and zones with id, map, area and time estimate |
| `roomba_plus.get_missions`, `get_mission` | all | Return the stored missions, or one in full |
| `roomba_plus.plan_clean` | Smart Map with the account, Prime | Returns what a clean would take, without starting it |
| `roomba_plus.explain_mission` | all | Returns the reason a mission was flagged unusual |
| `roomba_plus.maintenance_done`, `maintenance_snooze` | all | Mark a maintenance entry done, or silence it ([guide](maintenance.md)) |
| `roomba_plus.maintenance_forget_learned` | Classic | Forget the intervals learned from your replacements |
| `roomba_plus.reset_filter`, `reset_brush`, `reset_battery`, `reset_pad`, `reset_wheel_cleaning`, `reset_contact_cleaning`, `reset_bin_cleaning` | all | Record a part replaced or a task done now; the same as its button or `maintenance_done` |
| `roomba_plus.reset_robot_profile` | all | Forget what Roomba+ learned about the robot |
| `roomba_plus.create_backup`, `restore_backup` | all | Back up and restore everything Roomba+ stores for a robot ([Moving to Roomba+](../MIGRATING.md#replacing-or-selling-a-robot)) |
| `roomba_plus.debug_capture_start`, `_stop`, `_get` | all | A debug log without `configuration.yaml` or a restart ([Diagnostics](../reference/status-and-controls.md#debug-capture)) |
| `roomba_plus.create_schedule`, `update_schedule`, `delete_schedule` | Prime | Write the robot's schedules ([guide](schedules.md)) |
| `roomba_plus.set_quiet_hours` | Prime | Write the household's quiet hours ([guide](start-conditions.md#quiet-hours-do-not-disturb)) |

## Long-term statistics

**Applies to:** all robots

Roomba+ fills up to 365 days of mission history into Home Assistant's long-term statistics at every start. Add a *Statistics graph* card and search for `roomba_plus:`: three series, area cleaned, mission duration and missions completed.

## Reading the history from outside: the REST API

**Applies to:** all robots

```
GET /api/roomba_plus/{entry_id}/mission_history?format=summary|records|hazards|export|zone_coverage_health
POST /api/roomba_plus/{entry_id}/mission_history/import
GET /api/roomba_plus/{entry_id}/digest?date=YYYY-MM-DD
GET /api/roomba_plus/household
GET /api/roomba_plus/{entry_id}/mission/{mission_id}/explain
GET /api/roomba_plus/{entry_id}/mission/{n_mssn}/path
```

`.../mission/{n_mssn}/path` gives a mission's rooms in order with their times ("Kitchen at 09:05, Hallway at 09:23"), room by room rather than a pixel path. Every finished mission's coverage is also served as an image (`…/missions/latest/map.png`) for picture cards and notifications, or as coordinates (`map.json`).

Every parameter, response and a curl example: [REST API](../API.md).

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
