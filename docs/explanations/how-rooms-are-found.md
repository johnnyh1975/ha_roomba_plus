[Roomba+](../../README.md) › [Documentation](../README.md) › How a 900-series finds its rooms

# How a 900-series finds its rooms

A 900-series keeps no map of its own. Roomba+ divides the floor it has driven into areas; this is how, and what that can and cannot give you. Naming the areas: [Naming rooms and areas](../guides/clean-rooms.md#naming-rooms-and-areas).

**Applies to:** 900-series. Smart Map robots and Prime robots take their rooms from the robot's own map.

## How the areas are found

Every mission adds the floor the robot drove over to a coverage grid, the same grid the coverage map shows. The areas are found in that grid by its narrow points: a distance transform and a watershed, the core technique iRobot's own room-segmentation patent describes. Doors are where two areas meet.

- **Three missions first.** No areas are made until the grid has learned from three missions since it was last emptied. On incomplete coverage the method is unstable: a threshold the robot has not crossed yet looks like a wall, so it splits a room that is not split, and the split goes once a later mission drives through. One real archive showed 7 rooms after one mission, 6 after four and 5 once coverage settled; two of the seven were never rooms.
- **Only missions that started on the dock** count. Positions are measured from where a mission starts, so a mission started elsewhere lands turned and shifted against all the others (one tester got an area outside the house). Whether it started on the dock is read from the robot's own phase before the start: charging means on the dock. Such a mission is still drawn live but left out of the grid, the areas and the doors (`position_chain.missions_kept_out_of_grid` in the diagnostics). A start Roomba+ did not see, because Home Assistant started mid-mission, counts as on the dock.
- **Not a robot carried back.** A robot that got stuck and was carried to its dock docks metres from where it measured itself. A dock correction over 1.5 m is taken as carried: that stretch is drawn and kept, but left out of the grid, the areas and the doors, and not counted as drift.
- **The areas follow the coverage.** They are worked out again when the grid has grown or shrunk by 30 cells, or holds 30 cells no area has. An area the coverage no longer has goes, unless you named or hid it: a named room behind a door kept shut keeps its name and place.
- **Names stay.** When a named area is absorbed by another, the name moves to the area that took most of it, unless that one has a name of its own. When most of two differently named rooms fall into one area, the area is divided between them again.
- **A door is where the robot went through.** Rooms either side of a wall touch on the grid; a door is shown only once a mission's path went from deep in one area to deep in the other through it (`driven_by_missions` in the diagnostics). Until a mission has been checked, every door shows. Doors in the diagnostics carry `opening_mm` (the opening at its widest) and `constriction` (how much it narrows the floor).
- **The rooms it went into.** A whole-floor mission is recorded with the named rooms its position actually went into.

A new area raises a repair issue asking for a name; naming it also puts it into `select.{name}_zone_select`.

## Straight walls, doors and furniture

Where the robot's lanes run along two axes, the map draws each room as a rectangle at the outermost floor driven, one wall between two neighbouring rooms, doors as gaps where the robot went through (an opening much wider than a door as an open passage), and floor inside a room that was never driven as furniture. *Wall thickness* and *Door width* under Configure → Connection settings tune which sides are one wall and how wide a door is drawn. The diagnostics' `rooms.layout` says whether it is drawn and why not.

## What this can and cannot give you

A room here is the set of cells the robot drove over, so its shape follows coverage rather than walls: furniture leaves holes, edges sit about one robot radius short of the wall, and boundaries fall at narrow points in the coverage rather than at doorways. More missions fix the *count*; they do not fix the shape, because the sofa is in the same place every time. Outlines along the walls would need obstacle data the 900-series does not export.

A 900-series cannot be sent to an area: the robot has no command for it. The areas name where the robot is, what a mission covered and where it got stuck. If the rooms look wrong: [Troubleshooting](../TROUBLESHOOTING.md#rooms-are-wrong-or-fewer-than-expected).

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
