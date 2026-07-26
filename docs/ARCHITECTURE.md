# Architecture

Written after a review in July 2026, at ~45,000 lines of integration code and 4,200 tests. It
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

Three coordinators, each with a genuinely different shape. They are separate on purpose: mixing
differently-shaped sources into one would force every listener to ask what kind of update just
arrived.

**`IrobotCloudCoordinator`** (Classic) — polls iRobot's cloud REST API for mission history, maps
and geometry. The only polling coordinator.

**`PrimeCoordinator`** — push, no polling. Consumes `watch_mission_timeline()`, one event per real
mission event.

**`PrimeStatusCoordinator`** — push, no polling, but seeded once at startup. Carries all eight
named shadows plus the classic/unnamed one. Seeded because the push side only delivers *changes*,
so a slow-moving value like battery percentage would otherwise show nothing for hours.

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

It is **large**, currently 30+ fields mixing Classic-only health tracking (charge-cycle peaks,
dirt-density trends, battery-contact anomalies), Prime-only runtime state, and shared
infrastructure (coordinators, six stores). This is a known weakness rather than a design: each new
feature adds a field most consumers do not care about. Splitting it by responsibility is a
worthwhile future change.

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

- **`RoombaData` is a god object.** See above.
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
- **`config_flow.py` at ~45%** is the first thing every user touches, and the second-least tested.

---

## Testing

4,200 tests, ~35 seconds. More test code than production code.

Two conventions worth knowing:

- **Every new `EntityDescription` with a `translation_key` needs entries in all seven language
  files** before merge. Entity ids derive from the *translated* name on first registration, so a
  missing translation permanently mis-slugs entities for users in that locale. Guard tests enforce
  this.
- **Watch for over-broad `suppress`/`except`.** Three separate bugs in one session were hidden by
  error handling wide enough to swallow the evidence — including a test that suppressed the very
  exception proving it called a function that did not exist.
