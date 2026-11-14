[Roomba+](../../README.md) › [Documentation](../README.md) › Status, controls and diagnostics

# Status, controls and diagnostics

What the status entities say, what the controls do, and how to collect what a problem report needs. Every entity id: [Entities](entities.md).

## Status

**Applies to:** all robots. Most status sensors exist on both generations; [Entities](entities.md) says which.

| Entity | What it says |
|---|---|
| `sensor.{name}_phase` (*Status*) | What the robot is doing, in Home Assistant's language. Both generations report the same states; match the state value, not the displayed text |
| `sensor.{name}_error` (*Status – Error*) | The robot's error, in its own app's words: the Classic app's on a Classic robot, the Prime app's on a Prime robot, ours only for the battery and charging errors (101–121). `help_url` links iRobot's article for the error where its help catalogue has one ([why these words](../explanations/words-from-irobot.md)) |
| `sensor.{name}_readiness` | Whether the robot is ready to start, and why not. **The state is a key, not the displayed text** (`ready`, `off_dock`, `bin_full`), so an automation compares against those. A value Roomba+ cannot name yet reads `not_ready_<number>`. On a refusal the attributes carry the Classic app's words (`app_label`, `description`: what to do) and `help_url` |
| `sensor.{name}_job_initiator` (*Started by*) | Who started the current or last mission: schedule, iRobot app, dock button, Alexa, Siri, IFTTT, a teamed robot, Home Assistant, or Roomba+'s demand cleaning |
| `sensor.{name}_next_clean` | The next scheduled clean |
| `sensor.{name}_battery` | Charge in percent |
| `binary_sensor.{name}_connected` | Whether Home Assistant hears from the robot (Classic: the local connection; Prime: the cloud) |
| `sensor.{name}_ip_address`, `_rssi`, `_snr`, `_signal_noise` | Classic: address and Wi-Fi quality, off by default |
| `sensor.{name}_clean_base_status` | The Clean Base's own state: bag or tank missing, low, clogged, a sealing problem, bag full or almost full, an IR or motor problem, stopping, ready. This is the app's "docking station bag" indicator. `binary_sensor.{name}_bin_full` is the robot's own bin, rarely full with a Clean Base |

**Two states of *Status* worth knowing:**

- **Charging mid-mission**: the robot went back to top up and will carry on. Separate from plain charging, so an automation reacting to charging does not fire mid-clean.
- **No contact**: nothing has arrived for over an hour. Every other entity still shows its last value, which may be days old.

**Prime: cleaning or a dock task.** The phase sensor carries two attributes that are never both true:

```
cleaning    true during run, hmMidMsn, evac
dock_task   true during padWash, padDry, refill
```

A Combo that washes its pad *during* a clean is doing a dock task, not cleaning, by the vendor's own rule. An automation gating on "is it cleaning" goes false while the robot is plainly working, and `dock_task` says why.

**Prime: started by, and the last command.** *Started by* answers who started the mission; the attributes `last_command`, `last_command_by` and `last_command_time` answer what was last sent. The two can disagree: a mission started by a schedule while the last command came from the app.

### Missions and errors

| Entity | What it says |
|---|---|
| `sensor.{name}_mission_elapsed_time`, `_mission_start_time` | When the mission started and how long it has run |
| `sensor.{name}_mission_recharge_minutes`, `_mission_expire_minutes` | Countdowns: until the robot carries on after a mid-mission charge, and until the mission expires (off by default) |
| `binary_sensor.{name}_mission_active` | On for the whole mission, a mid-mission charge included |
| `binary_sensor.{name}_mid_mission_recharge` | On only during the mid-mission charge |
| `sensor.{name}_last_error_code` | The last error, with `description`, `action`, `text_source` (`classic_app`, `prime_app`, `roomba_plus`) and `help_url` |
| `sensor.{name}_last_error_at`, `_last_error_zone` | When and where |
| `sensor.{name}_stuck_count_30d` | Stuck events in the last 30 days (Classic) |
| `sensor.{name}_problem_zone` | The room the robot got stuck in most often over 30 days (900-series and Smart Map robots). Counts only missions sent to a single room, and no docking errors; empty unless one room leads with at least two |

Mission progress and the current room: [Progress and time estimates](../explanations/estimates.md). The mission record: [Mission history](../explanations/history.md).

### Cloud stream times

*New in 4.6.* **Applies to:** Prime · off by default

Four diagnostic sensors: when the state shadows, the mission timeline, the live map and the refusals stream last delivered a message, to the minute. A stream can stop delivering without raising an error, and the connection health sensor only sees errors. The diagnostics carry the same as `streams_seconds_ago`. A quiet stream is not a fault by itself: the refusals stream is silent unless the robot refuses something, the live map unless it runs.

## Controls

**Applies to:** each control exists only on robots that report the setting.

| Control | Type | What it does |
|---|---|---|
| Cleaning passes | Select | Auto / One pass / Two passes |
| Cleaning mode | Select | Vacuum / Mop / Vacuum and mop / Vacuum then mop. **Only the modes your robot can perform are offered**, read from the robot's own capabilities (all four until it has reported them). **Applies to every start**: room and zone cleaning (`clean_room`, `clean_zone`, the zone buttons, rooms picked on the card) and, on a robot that can mop, a plain start of the whole home (`vacuum.start`) the way the iRobot apps send it: Classic with the mode in the start command, Prime as every room of the current map with the mode. Classic never gets *Vacuum then mop* with a plain start; its app has no such value. Resuming a paused run sends no mode |
| Carpet boost | Select | Automatic / Eco / Performance (900-series) |
| Edge cleaning | Switch | |
| Always finish | Switch | Keep cleaning when the bin is full (robots with a Clean Base) |
| Schedule hold | Switch | Pause the schedule without deleting it (Smart Map robots, Braava) |
| Child lock | Switch | Disables the robot's own buttons |
| Eco charge | Switch | The robot's reduced-power charging |
| Gentle mode | Switch | Gentler vacuum and brushes, quieter (Classic) |
| Locate robot | Button | Plays the find-me tone |
| Evacuate bin | Button | Empties the bin into the Clean Base |
| Select room or zone | Select | Rooms and zones from every map in one list, on both generations. Each id carries its map, so a room upstairs can be mapped to an area and cleaned by name; rooms from two maps in one command are refused rather than half done. Attributes: `segment_type` (`room` or `zone`), and `segment_map`, `robot_on_map`, `robot_map_is_live` where the floors can be read |
| Clean selected room | Button | Sends the robot to whatever the select holds, on whichever map |
| *(one per favourite)* | Button | Runs a favourite saved in the iRobot app; it carries its own map, the shortest way to a room on another floor |

**A setting with an unusual value** shows that value rather than going unavailable. One Combo 515 holds a pad wash interval from another series, which the iRobot app shows as "not set"; selecting it again changes nothing.

**Experimental buttons, 900-series only** (off by default; enable them in the entity list):

| Button | What it does |
|---|---|
| Spot clean | Cleans a small area around the robot |
| Quick clean | A shorter mission over the whole floor |
| Sleep | Puts the robot into its low-power sleep |
| Power off | Switches the robot off completely |

Prime robots have their own settings (suction, pad wetness, washing, drying, auto-empty frequency): [Mopping robots](robots.md#mopping-robots) and [Entities](entities.md#selects).

## Diagnostics

### Debug capture

*New in 4.6.* **Applies to:** all robots

`roomba_plus.debug_capture_start` turns on debug logging for Roomba+, roombapy and roombapy-prime and keeps the lines in memory, for 30 minutes unless `minutes` says otherwise (at most 240), and at most 5,000 lines. `debug_capture_stop` ends it and puts the log levels back; `debug_capture_get` returns the lines. Both answer as an action response, so in *Developer tools → Actions* the result can be copied straight into an issue. No `configuration.yaml` and no restart.

The lines are redacted before they are kept: addresses (IPv4, IPv6, MAC), e-mail addresses, robot ids, tokens and keys, and the values of fields with private names (passwords, network names, positions). It is the same list the diagnostics use. Look the text over before posting it all the same: a room you named after a person is not something a filter can know.

### Diagnostics download

The device page → ⋮ → **Download diagnostics**. It covers the map, the zones, the room geometry, the cloud, the robot profile (battery chemistry and scale), the intervals learned from your replacements, the firmware of each component, and the field evidence the troubleshooting page asks for. No address, password, hash or coordinates. What to send with a report: [Reporting a problem](../TROUBLESHOOTING.md#reporting-a-problem).

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
