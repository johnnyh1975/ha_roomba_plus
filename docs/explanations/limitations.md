[Roomba+](../../README.md) › [Documentation](../README.md) › Known limitations

# Known limitations

What Roomba+ cannot do, and the ideas kept for later.

- **Room shapes on 900-series robots follow coverage, not walls.** A room is
  built from where the robot drove: furniture leaves holes, edges stop about one
  robot radius short of the wall, and boundaries land at narrow points rather
  than at doorways. More missions settle the number of rooms, not their shape.
  Rooms appear after three missions; see
  [How a 900-series finds its rooms](how-rooms-are-found.md).
- **A 900-series cannot be sent to a room.** The robot has no command for it;
  its areas name where it is, what a mission covered and where it got stuck.
- **600-series robots have no map**, so nothing that needs a position works on
  them: live map, rooms, room cleaning.
- **Stuck hotspots on i, s and j robots that send no position of their own**
  have not been seen with a real stuck incident yet. The coverage heatmap and
  layout-change detection on those robots are confirmed.
- **No voice sentences of its own.** A custom integration has no supported way
  to ship Assist sentences that work without a file you create yourself. The
  response actions (`get_rooms`, `plan_clean` and the others) are there for
  scripts that Assist can run.
- **No "retrain your Smart Map" reminder.** No signal was reliable enough at
  the level of the whole map.
- **Prime robots**: what is missing and what is not yet confirmed on a robot is
  listed in [V4/Prime robots](../PRIME.md#what-is-not-there).

## Planned, not scheduled

Ideas that are kept but have no release yet:

- **Furniture changes from the cloud map.** Detecting moved furniture from the
  differences between iRobot's own maps, for Smart Map robots. Layout-change
  detection today works from the local position grid.
- **Exporting room shapes and doors**, for use outside Home Assistant.

Evaluated and not pursued: voice sentences of its own (see above) and a "retrain
your Smart Map" reminder.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
