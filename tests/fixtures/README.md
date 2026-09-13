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
