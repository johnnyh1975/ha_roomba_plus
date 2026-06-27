# Roomba+ v2.10.2

Hotfix release. Five bugs found and fixed during a post-v2.10.1 audit of
the `mission_archive`/`mission_store` analytics pipeline and the new
RoomSegStore watershed engine introduced in v2.10.0. No config schema
changes; no card changes required.

## Fixed

### `mission_archive` — duplicate records from a pre-v2.8.6 bug, never cleaned up
The discontinuity-guard bug fixed in v2.8.6 (Round 1/2) stopped *new*
duplicate records from being appended, but never retroactively cleaned
up duplicates it had already written to disk before the fix shipped.
Archives that hit that bug while it was still active can have had a
block of missions tripled in the persisted `derived`/`timeline` arrays
ever since, silently inflating any analytics that aggregate over the
full archive.

Added a one-time `DEDUP-V1` pass in `MissionArchive.async_load()`,
gated by a persisted flag so it runs exactly once per archive and is a
no-op on every load after that.

### `MissionStore.area_sqft` — never backfilled from the cloud on accurate-timestamp robots
`area_sqft` is the canonical field read by `RobotProfileStore`'s mission
statistics and `compute_rolling_stats()`. Its cloud backfill was
implemented inside the timestamp-correction branch of
`backfill_from_cloud()` — so it only ever ran on the rare mission that
*also* needed a `started_at`/`ended_at` fix. On any robot whose local
timestamps are already accurate (the common case), `area_sqft` stayed
`None` forever regardless of how much cloud history was available, even
though the raw `sqft` field was being merged correctly the whole time.
The same gap existed in `merge_latest_from_cloud()`, the hook that runs
after every mission completion.

Both call sites now backfill `area_sqft` from `sqft` independently of
the timestamp-correction path, via a shared `_backfill_area_sqft()`
helper.

### RoomSegStore — every door's `saddle_mm` collapsed to the same value
The new (v2.10.0) watershed door-detection step computed each door's
saddle width as the *minimum* raw distance-transform value across the
*entire* shared boundary between two rooms — and that boundary almost
always contains at least one cell sitting right at the edge of the
visited mask, unrelated to the actual doorway. Confirmed in the field:
every door in a real archive measured exactly 1 grid cell (150.0mm),
regardless of true corridor width.

Fixed by computing the saddle on the Gaussian-smoothed distance field
(`dist_smooth`) instead of the raw one — the same field
`merge_regions()` already uses for its own boundary-saddle calculation.
Verified against the same real-archive fixture: five doors now measure
five different, geometry-correct values.

### RoomSegStore — doors silently deleted on recompute
`_match_doors()` rebuilt `self.doors` from scratch on every recompute,
dropping any existing door whose room pair wasn't re-detected that
round — along with its entire `observations` history and stable `id`.
This is inconsistent with the room-preservation policy already
documented and tested for rooms (`test_unmatched_existing_room_is_kept_not_deleted`).
It also isn't just theoretical: `GridStore` decays and prunes
low-traffic cells every mission, and a narrow, infrequently-crossed
doorway is exactly the kind of cell that can legitimately drop out of
the visited set for one recompute without the door having stopped
existing.

Unmatched existing doors are now kept, mirroring the room-preservation
policy, as long as both rooms they connect still exist.

### `RobotProfileStore.update_mission_stats()` — never wired into the callback chain
This method's own docstring has said "L3/L8 — called after each mission
from the callback chain" since at least v2.6.0. It had no caller
anywhere in the codebase, in production or in tests. Practical effect:
`mission_duration_mean`, `mission_duration_std`, and `mission_area_mean`
never populated for any installation, no matter how much mission
history existed — including, but not limited to, the `area_sqft` fix
above.

Wired into `_async_update_robot_profile_store()` (the existing L5/L6/J
callback chain) as the missing L3/L8 step. `update_mission_stats()` now
returns whether it wrote anything, matching the bool-return convention
already used by `update_lifetime_sqft_tracking()`.

## Testing

- 19 new regression tests added across `test_mission_archive.py`,
  `test_mission_store.py`, `test_room_segmentation.py`,
  `test_room_seg_store.py`, and `test_init_wiring.py`.
- Every fix in this release was verified to fail against the pre-fix
  code before being confirmed fixed — including a direct repro against
  a real, anonymised field archive for the `saddle_mm` bug
  (`tests/fixtures/sample_grid_980_og.json`).
- Full suite: **2,814 passing / 0 failing.**

## Upgrade notes

No action required. The `mission_archive` dedup pass and the
`area_sqft` backfill both run automatically on the next HA restart /
cloud refresh after upgrading. Existing RoomSegStore doors will pick up
corrected `saddle_mm` values on the next recompute that re-detects them
(unaffected doors keep their last-known value until then, per the
preservation fix above — they are not deleted or reset).
