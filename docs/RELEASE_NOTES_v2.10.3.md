# Roomba+ v2.10.3

Hotfix release. Three bugs found after v2.10.2: a structural discrepancy
between `format=summary` and `format=records`, a cloud-result classification
gap that caused error/cancelled missions to count as completed, and the
device tracker entity being invisible by default on all installations.
No config schema changes; no card changes required.

## Fixed

### `format=records` missing cloud-unmatched local missions (RECORDS-UNION)
`format=records` used a strict either-or source selection: when the cloud
coordinator was healthy and had any records, the entire local MissionStore
was ignored. Any mission that completed locally via MQTT but never appeared
in the cloud history feed (for whatever reason on iRobot's side) was
silently invisible in the records array, even though `format=summary`'s
`total` (always local) correctly counted it.

Confirmed in the field: `format=summary` showed `total: 3` for a day while
`format=records` returned only 2 records for the same day — the third was
a real, 38-minute completed mission that had no cloud counterpart.

Local records that never received a cloud merge signal (`dirt`/`chrgM`/
`wlBars` all absent — the same heuristic `cloud_coordinator.py`'s CR3
fallback uses) are now unioned into the cloud-sourced array and sorted by
`started_at`. The `source` field correctly marks these as `"local"`.

### `format=summary` counting error/cancelled missions as completed (B1-EXT)
`MissionStore._merge_cloud_fields()` only corrected one specific
cloud/local result mismatch: `pauseId=224` + local `"stuck"` → `"error"`
(map localisation failure). All other cases where the MQTT mission-end
packet carried a different result than what the cloud later revealed were
never corrected, leaving the local `result` field permanently wrong.

Confirmed in the field: the same day had `completed: 3` in `format=summary`
while `format=records` (cloud-sourced) showed `result: "error_battery"` and
`result: "cancelled_by_user"` for two of those missions — `completed: 0`
would have been correct.

Two new correction rules added to `_merge_cloud_fields()` (B1-EXT), applied
whenever the cloud record arrives via `backfill_from_cloud()` or
`merge_latest_from_cloud()`:

- `done == "bat"` with local `"completed"` or `"stuck_and_resumed"` → `"error"`;
  `error_code` backfilled from `pauseId` if absent.
- `done_raw == "usrEnd"` with local `"completed"` or `"stuck_and_resumed"` →
  `"cancelled"`.

`"stuck_and_abandoned"` is deliberately not touched — the robot stopped on
its own, independently of user or battery. Generic `"error"`/`"cancelled"`
values are used (not `"error_battery"`/`"cancelled_by_user"`) so the
corrected values stay within the documented local result enum.

### Device tracker entity invisible by default (all robot tiers)
`TrackerEntity.entity_registry_enabled_default` returns `False` when both
`mac_address` and `device_info` are `None`. Both are always `None` for
`RoombaDeviceTracker`: the robot is identified by BLID (not MAC), and
`device_info` is `None` by HA core design for device tracker entities.
The entity was registered correctly but disabled by default on every
installation, so it never appeared in the UI without manual intervention.

Confirmed root cause of Thonno's field report: "I don't seem to have that
entity on my i7+" — the entity existed in the registry but was invisible
on all tiers (SMART and EPHEMERAL alike).

Fixed by setting `_attr_entity_registry_enabled_default = True` explicitly
on `RoombaDeviceTracker`. Users who already have the entity in their
registry in a disabled state will need to enable it once manually; new
installations will see it enabled by default immediately.

## Testing

- 4 new regression tests for RECORDS-UNION (`TestLocalRecordHasCloudMergeSignal`,
  `TestRecordsUnionWithLocal`).
- 18 new regression tests for B1-EXT (`TestMergeCloudFieldsB1Ext`), including
  a direct field-bug repro against the real archive timestamps from 26.06.2026.
- 2 new regression tests for the device tracker fix
  (`TestEntityRegistryEnabledDefault`).
- Full suite: **2,836 passing / 0 failing.**

## Upgrade notes

No action required for RECORDS-UNION and B1-EXT — corrections apply
automatically on the next cloud refresh after upgrading.

For the device tracker: if you previously saw no `device_tracker.*` entity
for your Roomba, it now appears enabled by default after upgrading. If it
was already in your entity registry in a disabled state, enable it once
manually in Settings → Devices & Services → Roomba+ → the tracker entity.
