[Roomba+](../../README.md) › [Documentation](../README.md) › Mission history and statistics

# Mission history and statistics

What is recorded about each mission, and what Roomba+ learns from the history. Reading it from a script: [Scripts and Assist](../guides/scripts-and-assist.md); from outside Home Assistant: [REST API](../API.md).

**Applies to:** all robots for the mission log. The statistics built on iRobot's cloud history (performance, map intelligence, dirt trend) need a Classic robot with the iRobot account; the ones built on the position grid (stuck patterns) a Classic robot that reports its position.

> **A gap in the history is not a failure.** A robot that is stuck or in an error state does not attempt its scheduled missions, and iRobot records nothing for an attempt that never happened, so "no mission recorded" and "no mission attempted" look the same afterwards. One tester's robot sat stuck for 48 hours: two scheduled runs passed with no attempt and nothing in the history, while `phase` read `stuck` the whole time. Schedules run on the robot and in iRobot's cloud, not in Home Assistant.

## The mission log

Every mission is recorded in a log of up to 365 missions that survives a restart. Each also writes a Logbook entry and fires `roomba_plus_mission_completed` ([Events](../reference/events.md)).

| Sensor | What it says |
|---|---|
| Last mission result | How the last mission ended: `completed`, `cancelled`, `error`, `stuck`, `stuck_and_resumed`, `stuck_and_abandoned`. **The state is a key**: translated for display, compared as written here |
| Last mission duration, Last mission area | Minutes and area of the last finished mission |
| Last mission summary | The last mission in one entity: duration, area, battery used, recharges, dirt events, who started it, `scope` (`rooms` or `whole_home`), times |
| Clean streak | Days in a row with at least one completed mission |
| Missions last 30 days, Completion rate (30 days) | Completed missions, and completed against all |
| Area cleaned today | The day's area (robots that report area) |
| Rooms – Cleaning history | `{room: when it was last cleaned}` over all recorded missions, as the state's attributes and under `rooms`. Each room carries the time the robot finished it, not the end of the whole mission |
| Last cleaned – *room or zone* | One sensor per room and saved zone, off by default: Configure → Connection settings → *Separate sensor per room and zone*. The time the robot finished the room on its newest clean. Attributes: `last_duration_min` (time spent there, every visit added up), `typical_duration_min` and `typical_duration_cleans` (the median over the room's newest 10 cleans, and how many there were; charging and the drives to and from the dock are in neither), `region_id`, `pmap_id` |
| Consecutive anomalous missions | Unusual missions in a row (off by default) |
| Last mission team ID | The `team_id` of the last mission if it was part of an Imprint Link team clean (Classic, off by default) |

A multi-room mission is recorded up to about 90 seconds after the robot reports it done, so a pause between rooms is not taken for an early end.

**The error of a mission.** A mission's `error_code` falls back to the first error in its timeline when the record itself has none. On one tester's robot the record's own field was empty on all 49 missions while the timelines held 111 errors: sixteen missions had ended stuck. On Prime the last error is the newest mission that ended in error, read at startup and after each history sync.

## Rooms in the history

**Applies to:** Smart Map robots with the iRobot account · Prime

The vacuum entity carries the rooms of the mission:

| Attribute | When | What it says |
|---|---|---|
| `mission_scope` | during a mission | `rooms` when it was sent to rooms or zones, `whole_home` otherwise (a start from the robot's button, a 900- or 600-series always); `None` between missions. Kept with each mission as `scope` |
| `planned_room_order` | during a mission | The rooms in the order asked for. Empty for a start from the robot's own button |
| `mission_destination` | during a mission | The last room of `planned_room_order`: where the mission ends, not where the robot goes next |
| `room_progress_observed` | during a mission | Whether the robot has been seen to move on from the first room. `false` means the room is still the plan, not an observation |
| `last_cleaned_rooms` | after a mission | The rooms confirmed cleaned |
| `room_coverage` | after a mission | Per room, the share cleaned (0.0–1.0) |

- `sensor.{name}_room_areas`: `{room: floor area in m²}`, from the floor plan. The room sizes without a tape measure.
- `sensor.{name}_room_accessibility_scores`: `{room: {score, limiting_factor}}`, 0–100 from the stuck rate, the coverage gap and the time per area, each against this robot's own average over its other rooms. `limiting_factor` (`obstacle_density`, `coverage_gap`, `narrow_passages`) says what pulls it down. A room below 60 gets a repair issue.

## Unusual missions

**Applies to:** Classic; on Prime the count sensor only

Each mission is compared with the robot's own last 30 days. Two unusual missions in a row raise a repair issue; it needs 20 missions of history and clears when missions are normal again. With the iRobot account, the history in the account is used from the first day.

`roomba_plus.explain_mission` (and its [REST counterpart](../API.md#get-missionmission_idexplain)) gives the reason in plain words, `obstacle_or_blockage`, `excessive_recharge`, `dirt_spike` or `incomplete_coverage`, with a recommended action; `robot_lifted` and `error_code` come beside it, whatever the reason. Every `roomba_plus_mission_completed` event carries `anomaly_reason` and `recommended_action` already.

On a Classic robot the dirt count arrives with iRobot's mission history, minutes after the mission ended, so the end-of-mission event cannot report `dirt_spike`; `roomba_plus_mission_reassessed` does once the numbers arrive (with the iRobot account).

## Where the robot gets stuck

**Applies to:** Classic robots that report their position

- **The same place at the same time.** Every stuck event is kept per spot of the floor with its weekday and hour. A spot with 8 or more, over 60 % of them in one time slot, raises a repair issue ("Your Roomba gets stuck near Kitchen most often on Tuesday mornings"); it clears when the pattern changes.
- **Hotspots.** Neighbouring spots (15 cm each) that both qualify become one repair issue, with a compass bearing, the room on a Smart Map robot and the number of spots. It closes when the floor there is covered again, not when the lifetime stuck count falls, which it never does.
- On i, s and j robots that report no position of their own, the hotspots come from the cloud's data; that has not yet been seen with a real stuck incident.

## Performance and map statistics

**Applies to:** Classic robots with the iRobot account

| Sensor | State | Attributes |
|---|---|---|
| `sensor.{name}_cleaning_performance` | completion rate (%) | speed, trend, coverage_pct, clean_streak, `dirt_trend`, `dirt_cause` |
| `sensor.{name}_cleaning_analytics_30d` | cleaned area (m²) | time_h, dirt_density, recharge_pct |
| `sensor.{name}_wifi_health` | average Wi-Fi signal (%), from the bars over recent missions | `weakest_bucket_observed` (fewest bars seen, 0–4), `stability_pct` (spread in bars, not a percentage) |
| `sensor.{name}_event_counts_30d` | last error code | recharges, evacuations, dirt_events, error_time |
| `sensor.{name}_robot_health_score` | 0–100 from battery retention, navigation, cleaning speed trend, unusual missions and stuck rate | `status_text`, `recommendation`; Unknown until 20 missions |
| `sensor.{name}_map_learning` | how complete the Smart Map is (0–100 %), from the iRobot cloud | |
| `sensor.{name}_zone_summary` | number of clean zones | `keepout_zones`, `observed_zones` |

`sensor.{name}_integration_health` has the same `status_text` and `recommendation`. The single `recent_*` sensors are off by default; the four above hold the same figures.

**Why the floor is getting dirtier.** `dirt_trend` is rising, stable or falling, from the median of the five newest missions against the ten before. While it rises, `dirt_cause` says why: `brush_wear` (more dirt and *slower*: the sensor fires again on debris the brush does not pick up, and the robot slows down) or `floor_dirty` (more dirt at the same speed: the floor is dirtier). The first cleans after a gap of more than a week are left out: they run on an unusually dirty floor and would read as brush wear.

## Long-term statistics

Roomba+ fills up to 365 days of mission history into Home Assistant's long-term statistics at every start: area cleaned, mission duration and missions completed, under `roomba_plus:` in a *Statistics graph* card.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
