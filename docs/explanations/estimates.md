[Roomba+](../../README.md) › [Documentation](../README.md) › Progress and time estimates

# Progress and time estimates

How mission progress, the current room and the remaining time are worked out, and why they are estimates. Using them from a script (`plan_clean`, estimate quality, why a room taught nothing): [Scripts and Assist](../guides/scripts-and-assist.md).

**Applies to:** Smart Map robots with the iRobot account · Prime · 900-series where Roomba+ has found areas. A 600-series has no rooms.

## Mission progress

**Applies to:** Smart Map robots with the iRobot account · Prime · 900-series with areas

`sensor.{name}_mission_progress` is the mission's progress in percent, from per-room time estimates and the mission's effective time. It survives a restart of Home Assistant.

**Effective time.** `elapsed_run_min` is `mission_duration_min` (time since the mission started) minus `recharge_min` (the mid-mission charging the robot reported). Driving between rooms counts as cleaning time; only charging is left out. Both parts are attributes too.

**Where the per-room estimates come from**, in order:

1. iRobot's own estimate for the room, where the cloud offers one;
2. how long the room took your robot, averaged over its runs and kept separately per cleaning mode;
3. a whole-house average divided by the rooms of this mission, a last resort and a poor one.

A robot that decides its number of passes while running (auto pass mode) gets no estimate from the cloud: there is nothing to forecast before the run. It relies on the second source, and until a room has been measured, the percentage and `estimated_remaining_min` fall back to the robot's average mission length. A room without a figure from the cloud may gain one: iRobot builds its estimates from cleaning history.

Attributes: `current_room`, `current_room_source`, `next_room`, `elapsed_run_min`, `estimated_remaining_min`, `room_sequence`, `mission_duration_min`, `recharge_min`.

## The current room

**Applies to:** Smart Map robots with the iRobot account · Prime · 900-series with areas; by position only on robots that report or are asked for it

**The room follows the robot, not the clock**, where Roomba+ can tell where the robot is. Otherwise it is worked out from the elapsed time against the room estimates, and a room that takes longer or shorter than estimated moves the value on while the robot has not.

**From the position.** A robot that reports its position, or answers when asked for it, is placed in a room where that can be done: a Smart Map with rooms in the cloud, or a 900-series floor Roomba+ has divided into areas. A room counts once the robot has stayed in it for 15 seconds while cleaning, so crossing a doorway or a hall changes nothing. The display then follows the robot in its own order; `room_completed` fires for the room it left; the countdown covers the rest of that room and the rooms not yet entered. A whole-home run lists the rooms as the robot enters them. While positions arrive, phase changes and estimates do not move the room; when they stop for a minute, they take over again. The diagnostics show the rooms seen by position (`rooms.tracking.visited_rooms`, `position_room`).

**From the cloud's room events.** On Smart Map robots with the account and on Prime, the robot's own timeline says when it moves to the next room, and that moves the display too. A mission started in the iRobot app, by a schedule or on the robot has no plan in Home Assistant; on Prime the rooms the timeline names become the list, in the order the robot enters them, without a forecast of the rooms still to come and so without a remaining time.

A new start command while a mission runs replaces its room plan. If the progress ever sticks on a room the robot has finished, `roomba_plus.advance_room` moves it on.

`current_room_source` says which path produced the room:

| Value | Meaning |
|---|---|
| `position` | the room the robot's position puts it in |
| `estimate` | elapsed time against the room estimates |
| `mission_timer` | the mission timer's own value, when no estimates exist |

An automation that should act only on a verified room can check the source.

## Progress is an estimate, not coverage

**Applies to:** all robots with mission progress; area on robots that report it

`sensor.{name}_mission_progress` has two modes, and neither measures how much floor has been cleaned:

- **With a planned room order**: rooms completed against rooms planned.
- **Without one**: elapsed time against this robot's mean mission length.

The second is the common case on Prime, and it is convincing: it climbs at a steady rate and reads exactly like measured progress. It is arithmetic on the clock.

**It never reaches 100.** "Finished" is a change of phase, not a percentage, so the sensor stops at 99 and the end of the mission clears it. If the phase gets stuck, which can happen on the cloud's side, it stays at 99.

**Area is a different measurement.** `sensor.{name}_area_cleaned_today` sums the day's covered area and resets at midnight; `sensor.{name}_last_mission_area` is the area of the last finished mission. On some robots the area stays at zero while progress climbs; 600-series robots report no area at all. The unit follows **your Home Assistant unit system**, square feet or square metres; the iRobot account's own "Metric Units" switch is not in the data the robot sends.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
