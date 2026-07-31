# Architecture

Written after a review in July 2026, at ~45,000 lines of integration code and 4,282 tests. It
exists because none of what follows was written down anywhere — it lived in docstrings scattered
across 44 files, which meant every contributor had to rediscover it.

Kept deliberately short. Where a decision has a long history, the code says so at the point where
it matters; this file is the map, not the territory.

---

## Two connection types, one integration

The single most important thing to understand: a robot is either **Classic** or **Prime**, and
almost nothing about how they get their data is shared.

| | Classic | Prime (V4) |
|---|---|---|
| Transport | local MQTT/TLS on your LAN | iRobot's cloud (AWS IoT over WebSocket) |
| Library | `roombapy` | `roombapy-prime` |
| Setup path | `_phase_*()` in `__init__.py` | `_async_setup_entry_prime()` |
| Which one | `RoombaData.connection_type` — the single source of truth |

`connection_type` is the only correct way to ask. There is no boolean flag, no `if prime_robot is
not None`, no SKU check at runtime.

**SKU-based detection exists but belongs to setup only** — `is_prime_sku()` lives in
`roombapy-prime` (it is protocol knowledge, and the diagnostic tools need it too). It decides which
setup path a *new* config entry takes. After that, `connection_type` is authoritative.

---

## Where data comes from

Four coordinators, each with a genuinely different shape. They are separate on purpose: mixing
differently-shaped sources into one would force every listener to ask what kind of update just
arrived.

**`IrobotCloudCoordinator`** (Classic) — polls iRobot's cloud REST API for mission history, maps
and geometry. The only polling coordinator.

**`PrimeCoordinator`** — push, no polling. Consumes `watch_mission_timeline()`, one event per real
mission event.

**`PrimeStatusCoordinator`** — push, no polling, but seeded once at startup. Carries all eight
named shadows plus the classic/unnamed one. Seeded because the push side only delivers *changes*,
so a slow-moving value like battery percentage would otherwise show nothing for hours.

**`PrimePartsCoordinator`** — the one genuinely polling coordinator, every six hours. Consumable
parts come from a REST endpoint that nothing pushes, so it has to ask. It asks rarely: consumables
move on a scale of cleaning hours, and hammering someone else's cloud API for a number that shifts
a few times a day would be rude.

Classic's own live state does not go through a coordinator at all — it arrives via callbacks
registered on the `roombapy` object (`callbacks.py`).

### One thing worth knowing about push streams

A push stream that stops **delivering** without ever **raising** is invisible to error handling —
the generator simply never yields again. Both Prime coordinators therefore write
`RoombaData.last_mqtt_message_ts` on every message, which the staleness checks read. This was
missing for Prime until a field report where two independent sensors froze while the integration
reported itself healthy.

---

## Reconnects live in the library, not here

`roombapy-prime` owns all reconnection, with its own backoff and a lock that stops concurrent
watchers tearing down each other's shared connection. This integration calls `connect()` once and
consumes the resulting generators.

The outer retry loops in `prime_coordinator.py` are a safety net for the generator dying entirely,
not a second reconnect layer. If one fires, something unusual happened.

---

## Where state lives

`RoombaData` (`models.py`) is `config_entry.runtime_data` — one object per robot, reachable from
every entity.

It is **large** — 47 fields — mixing Classic-only health tracking, Prime-only runtime state and
shared infrastructure (coordinators, ten stores).

### Which stores each generation gets, and why

Ten persistent stores exist. Classic robots get all of them; Prime robots get four, and the split is
not arbitrary.

| Store | Prime | Reason |
|---|---|---|
| `mission_store` | yes | Filled from the cloud's mission history over REST |
| `maintenance_store` | yes | Records when the USER changed a part — never generation-specific |
| `mission_timer_store` | yes | Driven by phase transitions, which Prime reports |
| `robot_profile_store` | yes | Its `update_mission_stats` half works on plain mission records |
| `geometry_store` | no | Derived from pose data |
| `grid_store` | no | Derived from pose data |
| `room_seg_store` | no | Derived from pose data |
| `outline_store` | no | Derived from pose data |
| `trajectory_store` | no | Derived from pose data |
| `freeze_snapshot_store` | no | Exists solely to back up the pose-derived stores against a firmware change that stops pose delivery. For a robot that never delivered poses it has nothing to protect. |

All four Prime stores were left at `None` from v4.0.0a0 until v4.0.0a14 — around 30 sensor lookups
read from nothing while the data was available. **Creating a store was only half of each fix**: the
Prime branch of `sensor.async_setup_entry` returns before the mission sensors are built, so a filled
store still had no reader; and `mission_timer_store` needed phase transitions fed to it or it would
have persisted an empty file forever.

That shape — working data that no entity consumes — has appeared three times in this project.
`PrimeMapImage` was unreachable for a whole release because `IMAGE` was missing from
`PRIME_PLATFORMS`, and `CLEAN_AREA` was advertised without the method that supplies the room list.
It is hard to diagnose because every individual piece tests fine.

**Size alone turned out not to be the problem.** A review looked for the usual suspects and found
none: no mutable-default bugs, no paired fields drifting apart, and each writer coherent within its
own module. Most fields are read by five to seventeen modules, which is exactly what a
`runtime_data` container is for.

What it *did* find was four fields left behind when v3.5.0 removed the Repair Issues they fed. Two
were never written again; two were recomputed on every cloud update — including a median over ten
mission records, in two separate places — for a consumer that had not existed for two minor
versions. All four are gone, and a guard test now names any field nothing outside `models.py`
references.

**The rule that makes a container this size decidable** is in the class docstring: ephemeral state
belongs here, anything that must survive a restart belongs in a Store. That rule was previously
written on two of the dead fields and would have been deleted with them.

Persistent state lives in stores under `hass.storage`, keyed by entry id — `MissionStore`,
`MissionArchive`, `GeometryStore`, `GridStore`, `RobotProfileStore`, `OutlineStore` and others.

**`STORAGE_VERSION` is deliberately two separate numbers**: `_HA_STORE_VERSION` (pinned, passed to
`Store()`) and `PAYLOAD_VERSION` (our own marker). Conflating them raises `NotImplementedError` on
upgrade for every existing install.

---

## Migrations

`migrations.py`, ~2,100 lines, one block per schema version with the full history in its docstring.

Migrations only ever grow: every historical version has to stay forever, because somebody is still
on it. Nothing here is ever deleted.

`__init__.py` re-exports `async_migrate_entry` because Home Assistant looks for it on the
integration module. Dropping that re-export would not fail loudly — HA would conclude there are no
migrations and silently stop upgrading every existing install. A test guards it.

---

## Known weaknesses

Recorded because a review that stays in one person's head is not a review.

- **Delayed saves need an explicit flush on unload, and it is easy to forget.** Both
  `MissionTimerStore` and the Prime map PNG persist via `Store.async_delay_save`, which coalesces a
  burst of writes into one. Without a matching `async_save` on unload, a RELOAD can have the old
  instance's pending write land after the new instance has already loaded — overwriting fresh state
  with stale, which is worse than losing the update.

  Classic solved this for `MissionTimerStore` in v3.3.0. The Prime unload path was written later and
  did not inherit it: its short `CLOUD_ONLY` branch returns before that code is reached. The Prime
  map then reproduced it a third time, having copied the delayed-save pattern from that very store
  without copying the flush. Any new delayed-save store needs the flush added deliberately; nothing
  enforces it.

- **The Prime and Classic branches diverge silently.** `async_setup_entry`, `async_unload_entry` and
  most platform setups have a `CLOUD_ONLY` branch that returns early. Anything added to the Classic
  path afterwards is not inherited, and nothing flags it. Three separate gaps were found this way in
  one session: the timer flush above, the long-term statistics backfill, and the hour meter a
  maintenance reset is recorded against — which recorded 0 for Prime, making every interval since a
  reset read as the robot's entire lifetime.

  The useful question when touching either path: *what does the other branch do here that this one
  does not?*

- **Entities cost cloud requests, and nothing measures that.** Two bugs in one session came from the
  same question — *what does this cost per day?* — rather than from anything failing.

  `SwitchEntity` polls every 30 seconds by default. The schedule switches read from the cloud in
  `async_update`, so three schedules meant roughly 8,600 requests a day for data that changes when
  somebody edits a schedule in the iRobot app. And the Prime room map called `get_map_metadata()`
  twice per refresh — for identical data, while the comment beside the second call claimed it reused
  the first.

  Neither shows up in a test. Both were found by counting requests per entity per day, which is
  worth doing whenever a new entity or a new refresh path is added.

- **`image.py`, 2,546 lines, and `_handle_mission_end()` inside it.** Eight releases have patched
  that one method, and **four of those were ordering fixes** — each shipped as a real bug first.

  Splitting it into named steps was considered and rejected: the dependencies do not flow through
  values but through seven shared stores as side effects. A signature cannot express "this must run
  after GridStore already contains the current mission", so extraction would move the constraint
  further from the code that depends on it. The ordering constraints are now stated together at the
  top of the method, and two of them are enforced by a test — verified by reintroducing the real
  v3.2.1 bug and watching it fail.

  Splitting the file's four classes into separate files was also considered and rejected: they
  share five helper functions, so it would trade one file for five plus new import coupling, for no
  benefit beyond a shorter scroll.
- **`vacuum.py` checks `connection_type` at six separate points** inside one class. Four identical
  copies of the same transport branch were collapsed into `_async_send_verb()`; the remaining ones
  differ in behaviour rather than merely in transport.

  A first version of this note proposed an `IRobotVacuumPrime` subclass. **That was wrong**, and
  the reason is recorded here so nobody tries it: the entity class is chosen by *device capability*
  (BraavaJet / RoombaVacuumCarpetBoost / RoombaVacuum), and connection type is orthogonal to it. A
  Prime robot can be any of the three, so subclassing would need `BraavaJetPrime`,
  `RoombaVacuumCarpetBoostPrime` and so on — a combinatorial explosion to remove one if-statement.
- **`sensor_core.py` sits at ~42% coverage**, up from 32%. Consumable arithmetic, the countdown
  tick and availability logic are now covered; the remainder is `extra_state_attributes` and the
  schedule-parsing helpers.
- **`config_flow.py` at ~51%**, up from 45%. The setup error paths a user hits before they have any
  entity or log to inspect are now covered — wrong credentials, rate limiting, certificate
  problems, and the pairing step's two fallbacks to manual entry. The remainder is discovery and
  reconfiguration.

---

## Testing

4,282 tests, ~40 seconds. More test code than production code.

Two conventions worth knowing:

- **Every new `EntityDescription` with a `translation_key` needs entries in all seven language
  files** before merge. Entity ids derive from the *translated* name on first registration, so a
  missing translation permanently mis-slugs entities for users in that locale. Guard tests enforce
  this.
- **Watch for over-broad `suppress`/`except`.** Three separate bugs in one session were hidden by
  error handling wide enough to swallow the evidence — including a test that suppressed the very
  exception proving it called a function that did not exist.
