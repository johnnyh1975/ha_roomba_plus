[Roomba+](../../README.md) › [Documentation](../README.md) › How the maps are drawn

# How the maps are drawn

Frames, mirroring and rotation, the path of a robot that is asked for its position, and the checks that watch the floor over time. What the map images are and how to show them: [Maps and dashboards](../guides/dashboards-and-maps.md).

**Applies to:** Classic robots with position sensing (900, i, s, j, Braava m). Prime maps come drawn from the robot's own map bundle and keep the iRobot app's orientation.

## Frames, mirroring and rotation

**Applies to:** Classic robots with position sensing; rotation on every map picture

**Two orders of x and y.** The maps draw the robot's position with x and y swapped, as the firmware's own client does. The tracker's `x_mm`/`y_mm` keep the firmware's order; `map_x_mm`/`map_y_mm` are the same point in the frame of the maps, the cleaning path and the stuck pins, and are the pair to use when placing the robot on a map.

**That frame is a mirror image of the floor**: swapping two axes reflects, it does not turn. So the pictures in the robot's own frame are drawn mirrored back in x (the cleaning path, the coverage map on a 900-series, the areas in *Rooms & zones*) and show the floor the right way round. The millimetres are not touched: `map_x_mm`, `rooms`, the hazards and everything stored keep their frame; only the step to pixels mirrors. Pictures in the cloud map's frame (the rooms map, the coverage of the last mission and the coverage map of a Smart Map robot) are not mirrored. The coverage map's `mirror_x` attribute says which applies.

**A path asked from a Smart Map robot is in the cloud map's frame**, not the dock's (seen on two i-series on lewis firmware). It is drawn as the rooms map draws the cloud map, so the path, the rooms map and the coverage map show the floor the same way round.

**Turn the map to suit you.** The map has no north: its top is the way the robot faced as it left the dock, so it depends on how the dock stands. Configure → Connection settings → *Rotate the map picture* turns every picture clockwise by 90, 180 or 270 degrees: the cleaning path with the robot's heading, doors and overlays, the area map, the coverage map, the cloud coverage, and the rooms map with its published pixel positions. Nothing stored moves. A Smart Map's pictures start in the map's own orientation, the one it had right after mapping, not the turn set in the iRobot app; this option is how to match the app.

## The path of a robot that is asked for its position

**Applies to:** Smart Map robots that send no position of their own

Some Smart Map robots send no position of their own; Roomba+ asks them about once a second while they clean ([Maps and dashboards](../guides/dashboards-and-maps.md#the-cleaning-map)).

- **On the floor plan.** The rooms of the cloud map are drawn under the path, with their names when *Draw room names on the map image* is on, and the picture fits the whole floor plan from the start. The cleaned area is laid over the floor plan at half opacity, so the rooms show through; the path stays opaque, and the room names are drawn over it.
- **A map turned in the iRobot app** turns the positions the robot answers, once the robot has saved a map with that turn after a cleaning run. Roomba+ turns them back by the angles of the map version the robot saved, so the path lies on the rooms map.
- **Align the path with the rooms** (Configure → Connection settings) replaces that with a fixed angle for a robot where it reads wrong. The path already drawn turns as soon as the setting is saved, during a run and after it, so an angle can be checked against the floor plan. Room tracking uses the same turned positions, so a path that lies beside its rooms also puts the robot in the wrong room; *Rotate the map picture* turns the whole picture and does not change that. The diagnostics show the angle read (`requested_path_turn_deg`), where it came from, the one drawn and `requested_path_turn_in_picture_deg`.
- **Automatic learns the turn from the path.** The map's own angles were wrong on two of the three robots whose owners checked (an i7+ read −90° where 0° was right, a j7+ 180°). So every position is also tried at 0°, 90°, 180° and 270°, and counted per map: how many land in a room. Once a map has 300 positions (about five minutes of cleaning) and one angle puts at least half of them in a room and half as many again as the angle in force, *Automatic* takes it, and the path already drawn turns with it. A map turned in the iRobot app gets new angles and starts its counts afresh. A fixed angle is never changed. The diagnostics show the counts (`path_turn_learning`) and `requested_path_turn_source: learned` once they decide.
- **What it does not do yet:** such a path is not fed into the coverage heatmap, the door markers or room learning, and the map carries no `calibration_points`/`rooms` while it shows one; the image says `position_source: request` instead. The robot reports the position from its dock, as a 900-series does; that this matches the cloud map's frame has not been checked on any robot yet.
- **When the robot does not answer.** A robot that never answers, or has no first position two minutes after the start (a Braava jet m6 answered without one for a whole run), is not asked again until its next mission. A robot that answered before and falls silent is asked again after a pause (30 s, then 60 s, then every 2 minutes, up to ten times a mission): an i7+ once left three requests unanswered for a minute and then answered them all at once. The diagnostics say which (`position_chain.live_position`: `status`, `stalls_this_mission`).

## Map drift

**Applies to:** 900-series

A repair issue (`map_drift_detected`) comes when the robot's recent missions end clearly further from where the dock should be than usual, judged over the last 10 missions rather than the robot's whole life, and goes once that is back to normal. It usually means the dock was moved or the robot has trouble finding itself; put it back on the dock and let it run a fresh mission if it stays.

## Layout changes

**Applies to:** Classic robots that report their position, and Smart Map robots with the iRobot account

`binary_sensor.{name}_layout_change_detected` turns on when a spot that was reliably covered for 20 or more missions has been missed by 3 in a row: new furniture or another obstacle may be in the way. Only missions sent everywhere count; a mission sent to rooms or zones says nothing about the rest of the house.

A repair issue comes for each patch of neighbouring spots, at its centre. Dismiss it for 30 days if the change was expected (a rug added on purpose); a patch that grows is shown again, and an issue whose spots are covered again goes. The binary sensor always shows the actual state, dismissed or not, so automations see reality.

It needs 23 missions of history per spot before it can judge; `cells_tracked` and `missions_until_first_ready` show the progress from the first mission. On i, s and j robots that send no position of their own, the history is built from the cloud's data once the positions are lined up with the floor plan, usually after a handful of missions. The same spots are in the map images' `furniture_candidates` attribute.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
