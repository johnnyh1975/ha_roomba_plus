[Roomba+](../../README.md) › [Documentation](../README.md) › Start only when the house is ready

# Start only when the house is ready

Hold a start back while a door is open or someone is home, send a paused robot home, and what a Prime robot's own quiet hours and start blocks do. Schedules that wait until everyone is out: [Schedules, presence and demand cleaning](schedules.md#presence-aware-scheduling).

## Blocking sensors and Smart Start

**Applies to:** all robots

Keep a clean from starting while a door is open, a room is occupied or people are home.

Configure → **Blocking sensors**

| Option | Values | Default |
|---|---|---|
| Blocking sensors | Any binary sensor entity IDs | (empty) |
| Behavior when blocked | `abort` or `queue and wait` | `queue and wait` |
| Queue timeout | 5–120 min | 30 min |

```yaml
action: roomba_plus.smart_start
target:
  entity_id: vacuum.roomba
data:
  override_blocking: false   # set true to bypass sensors in this call
  rooms:                     # robots that clean by room; omit for the whole home
    - Kitchen
    - Hallway
```

- **abort**: fires `roomba_plus_start_blocked` at once if any sensor is on.
- **queue**: waits until all sensors are off (up to the timeout), then starts; fires `roomba_plus_start_timeout` if the time runs out.
- An unavailable or unknown sensor does not block.
- A sensor that is on also holds back demand cleaning.

`binary_sensor.{name}_start_blocked` is on while a start waits, with the attributes `blocking_entities`, `queued_since` and `timeout_at`. Both events carry `entry_id` and `name`, so an automation can tell which robot; `event.<robot>_command` mirrors them ([Events](../reference/events.md)).

## Send home after a pause

*New in 4.6.* **Applies to:** all robots · off by default

Configure → Connection settings → *Send home after a pause (minutes)*. A robot that
stands paused that long is sent back to its base through
`vacuum.return_to_base`. 0, the default, turns it off. By the vacuum's state,
so both generations behave the same; the timer restarts on every change of
state and the state is read again before the robot is sent.

## Quiet hours (Do Not Disturb)

**Applies to:** Prime

Quiet hours are two mechanisms plus a display, and the robot's protocol keeps
them separate:

| | |
|---|---|
| `roomba_plus.set_quiet_hours` | writes the daily window, or a one-off "quiet until" moment |
| `switch.{name}_prime_quiet_hours_active` (shown as *Do not disturb*) | turns DND on and off right now |
| `binary_sensor.{name}_prime_quiet_hours` (shown as *In quiet hours*) | whether a scheduled window covers this moment |

The action takes **either** a daily window (`start` and `end`) **or** a single
`ends_at`; the robot's own format has exactly those two
cases, and sending both returns an error. It is household-wide: every robot on
the account is affected, and the entity you target is only how the household is
found.

The switch and the sensor answer different questions and can legitimately
disagree: DND switched on while no scheduled window covers right now. The
switch has no shadow field to read back from, so its state is what it last
sent and restores across a restart; a change made in the iRobot app will not
show up there.

**Its effect is unproven.** A Prime robot has been observed cleaning inside its
own quiet-hours window. The setting is accepted and reads back; whether the
robot honours it is a separate question no field report has answered yes. Keep
automations that depend on quiet hours in Home Assistant rather than relying on
the robot.

## When a Prime robot cannot start

**Applies to:** Prime

`binary_sensor.{name}_prime_start_blocked` turns on when one of four faults would
stop a mission from starting, and says what still works:

```
blocked_modes    ["vacuum"]
available_modes  ["mop"]
blocking_faults  [287]
blocked_reason   "Unable to vacuum: remove Pad Plate"
```

Three of the four do not mean the robot is broken; they mean one half of what
you would ask is impossible right now. `287` is the pad plate fitted (mop
works), `290` the plate missing (vacuum works), `234` the plate fitted with no
cloth on it (vacuum works), `286` the robot off the floor (neither).

**A start is refused only for what the fault rules out**: mopping
with `290` or `234`, vacuuming with `287`, anything with `286`, with the
vendor's text for the fault. A start that names no mode is refused only with
`286`. Docking, stopping and pausing are never held back, nor are the dock
controls. A device trigger, *Start blocked by a fault*, fires when a block
appears.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
