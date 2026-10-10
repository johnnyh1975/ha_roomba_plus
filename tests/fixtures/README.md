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

## tester_980/ — a second home for the 900-series areas

A tester's Roomba 980, from his Roomba+ backup of 6 October 2026, kept
**with his permission** (October 2026). Three stores, as the backup holds
them: `grid.json` (coverage grid, 2984 cells), `roomseg.json` (eight
areas) and `missions.json` (sixteen records). Left out on purpose: the
backup's manifest (it carries the BLID), the trajectories, the floor
plans he shared for the analysis, and every other store. None of the
three files holds a BLID, a credential or an address.

His home fails the opposite way to the maintainer's: furniture splits
his dining room and kitchen, where walls split the maintainer's flat. A
change to the area logic that helps one and harms the other has been
proposed before (hole filling, see the project notes), so every such
change runs against both.
Tests: `TestTheSecond980sHome` in `test_room_seg_store.py`,
`TestTheSecond980sWholeStore` in `test_mission_store.py`.

## roomba_plus_missions_02_01KRRVYR4T1MPSYM7ACKA5XCBX.dms — the maintainer's 980, 9 October 2026

The whole mission store of the maintainer's own Roomba 980, 79 records
up to mission 458. Room names are replaced by `Room A`, `Room B`,
`Room C`; nothing else is changed. No BLID, credential or address.

It is the store that showed one mission recorded twice: once by the
robot, once taken from the cloud. A 980 mission that ends in an error
(17, 2, 4) is closed by the cloud at the error, while the robot reports
it over hours later, so the two records share their start and not their
end. Fifteen of the 79 records are such cloud copies; eight more are
cloud records of missions with no local record, which stay.
Tests: `TestTheCloudsCopyOfAnErrorMission` in `test_mission_store.py`.
