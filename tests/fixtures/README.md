# Test fixtures

Real captures, not generated data. Each is a recording from somebody's
robot or from iRobot's own cloud, kept because this project has repeatedly
found that a synthetic payload proves only that its author understood the
code they were testing.

Most are referenced by exactly one test. That is not duplication waiting
to be tidied — it is one capture answering one question.

## vendor_mission_history.json — kept deliberately, currently unused

Twenty mission records from a **mop**: `detectedPad` cycles through
`dispDry`, `dispWet` and `padPlate`, the commands include `clean` as well
as `start`, and the region entries carry `region_name`.

No test reads it today, which makes it look like an orphan. It is not:
`irobot_missionhistory_i3plus.json`, the only other mission-history
capture here, has three records from a vacuum and a different key set
(`dirt` rather than `detectedPad`). Deleting this one would lose the only
mop mission history the project has, and captures are the one thing that
cannot be recreated by writing more code.

If you are adding tests for mop mission handling — pad type per mission,
`clean` versus `start`, region names straight off the wire — this is the
data to use.

## liblit_980/ — a second home for the 900-series areas

@liblit's Roomba 980, from his Roomba+ backup of 6 October 2026, kept
**with his permission** (October 2026). Three stores, as the backup holds
them: `grid.json` (coverage grid, 2984 cells), `roomseg.json` (eight
areas) and `missions.json` (sixteen records). Left out on purpose: the
backup's manifest (it carries the BLID), the trajectories, the floor
plans he shared for the analysis, and every other store. None of the
three files holds a BLID, a credential or an address.

His home fails the opposite way to the maintainer's: furniture splits
his dining room and kitchen, where walls split the maintainer's flat. A
change to the area logic that helps one and harms the other has been
proposed before (hole filling, see `LIBLIT_980_ANALYSE.md` in the
project notes), so every such change runs against both.
Tests: `TestLiblitsHome` in `test_room_seg_store.py`,
`TestLiblitsWholeStore` in `test_mission_store.py`.
