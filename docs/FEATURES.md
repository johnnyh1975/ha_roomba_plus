# Roomba+ — Feature Reference

[← Roomba+](../README.md)

Full reference for every entity, service, and configuration option. For a
quick "does this work on my robot" overview, see the capability matrix in
the [README](../README.md#supported-hardware--capability-matrix). For
copy-paste automation examples, see **[Automations & dashboards →](AUTOMATIONS.md)**.

## Contents

- [Tier legend](#tier-legend)
- [Status, control & error intelligence](#status-control--error-intelligence)
- [Room cleaning, zones & maps](#room-cleaning-zones--maps)
- [Maintenance & health](#maintenance--health)
- [Mission history & room intelligence](#mission-history--room-intelligence)
- [Presence & scheduling](#presence--scheduling)
- [Connectivity, mop & configuration reference](#connectivity-mop--configuration-reference)
- [Events & device triggers](#events--device-triggers)

## Tier legend

Every feature below is tagged with which robots support it:

| Tag | Meaning |
|---|---|
| `[ALL]` | Every supported robot, including 600-series (bump-and-run) |
| `[900+]` | 900-series (VSLAM) or better — needs at least a live/ephemeral map |
| `[SMART]` | i/s/j-series or Braava m6 (persistent Smart Map) — cloud not required |
| `[SMART+CLOUD]` | Smart Map robot **and** iRobot cloud credentials configured |
| `[BRAAVA]` | Braava m6 (mopping) specific |
| `[PRIME]` | V4/Prime generation — Roomba Max, Combo 400-series and other robots on iRobot's newer cloud protocol |

**Classic and Prime are different generations, not tiers of one.** A Classic robot
talks to Home Assistant over your own network and can work with no internet at all;
EPHEMERAL and SMART describe how much map it keeps. A Prime robot has **no local
connection** — everything goes through iRobot's cloud — and it is not supported by
the stable v3.x line at all.

Sections below that carry no tag apply to Classic robots. Prime-only entities are
marked in their own headings.

---

## Status, control & error intelligence

**Status sensors:**

| Sensor / Entity | Notes |
|---|---|
| Phase | Idle / Stopped detection beyond the standard HA states |
| Error code | 80+ error codes with `description` and `action` attributes |
| Readiness | Whether the robot is ready to start |
| Job initiator | Who triggered the current mission (`schedule`, `manual`, `demand`) |
| Next scheduled clean | From `cleanSchedule2` or legacy `cleanSchedule` |
| Battery level | Charge percentage |
| Connected | MQTT connectivity binary sensor |
| IP address | Current robot IP |
| RSSI / SNR / Signal noise | Wi-Fi signal quality |

**Controls:**

| Control | Type | Notes |
|---|---|---|
| Cleaning passes | Select | Auto / One pass / Two passes |
| Carpet boost | Select | Automatic / Eco / Performance (900-series) |
| Edge cleaning | Switch | |
| Always finish | Switch | Keep cleaning even when bin is full (i7+/s9+/j7+ with Clean Base) |
| Schedule hold | Switch | Freeze schedule without deleting it (i/s/j/Braava) |
| Locate robot | Button | Play find-me tone |
| Evacuate bin | Button | Clean Base models only |

**Actions** (Settings → Automations → Actions → Roomba+):

| Action | Robots | Description |
|---|---|---|
| `roomba_plus.smart_start` | All | Start with blocking-sensor gate; optionally targets rooms on SMART robots |
| `roomba_plus.clean_room` | SMART | Clean one or more named rooms — no HA 2026.3+ required |
| `roomba_plus.clean_zone` | PRIME | Clean one or more zones on demand, by name or numeric id |
| `vacuum.clean_area` | SMART + cloud + HA 2026.3+ | Clean by HA area — see [Room cleaning setup](#room-cleaning-setup--ha-areas-vacuumclean_area-ha-20263) |
| `roomba_plus.reset_filter` | All | Record filter replacement |
| `roomba_plus.reset_brush` | All | Record brush / pad replacement |
| `roomba_plus.reset_battery` | All | Record battery replacement |
| `roomba_plus.reset_wheel_cleaning` | All | Record wheel module cleaning (v2.7+) |
| `roomba_plus.reset_contact_cleaning` | All | Record charging contact cleaning (v2.7+) |
| `roomba_plus.reset_bin_cleaning` | All | Record bin cleaning (v2.7+) |
| `roomba_plus.reset_robot_profile` | All | Wipe learned calibration data (v2.7+) |
| `roomba_plus.clean_sequence` | All | Start robot B when robot A finishes |
| `roomba_plus.advance_room` | SMART + cloud | Manually advance mission progress to the next room when it gets stuck on a completed one (v2.8.0) |
| `roomba_plus.clean_overdue_rooms` | SMART + cloud | Clean every room currently overdue (configured or learned rhythm), travel-optimized route from the dock (v3.3.0) |
| `roomba_plus.auto_clean_dirty_rooms` | SMART + cloud | Clean the rooms that are dirtier than your household average, travel-optimized (v3.3.0) |
| `roomba_plus.explain_mission` | All | Plain-language reason for a flagged mission anomaly (v3.2.0) |

**Device triggers** for automations:

| Trigger | Description |
|---|---|
| Cleaning started | Robot transitions into an active cleaning phase |
| Cleaning finished | Robot returns to dock after completing a mission |
| Robot stuck | Robot reports a stuck condition |
| Bin full | Dust bin is full |
| Docked | Robot is docked and charging |
| Error reported | Robot reports any error |

**Experimental buttons — 900-series only** (disabled by default, enable via entity list):

| Button | What it does |
|---|---|
| Spot clean | Cleans a small area around the robot's current position |
| Quick clean | Shorter full-floor mission |
| Sleep | Sends the robot to low-power sleep state |
| Power off | Powers the robot off completely |

#### Child lock, eco charge & gentle mode switches (v3.2.0, v3.4.3)

Three config-category switches, each created only on models that report the underlying preference: **Child lock** (`childLock`) disables the robot's physical onboard buttons — useful for households with kids or pets that might otherwise trigger a clean by accident. **Eco charge** (`ecoCharge`) toggles the robot's reduced-power charging mode. **Gentle mode** (`gentle`, v3.4.3) reduces vacuum/brush aggressiveness for quieter, gentler cleaning — useful for a sleeping baby or a nervous pet. `sensor.*_dock_firmware_version` (diagnostic, disabled by default) exposes the dock's own firmware version separately from the robot's.

---

## Room cleaning, zones & maps

#### `clean_room` vs `vacuum.clean_area`

Two ways to clean specific rooms — choose based on your HA version and setup:

| | `roomba_plus.clean_room` | `vacuum.clean_area` |
|---|---|---|
| HA version | Any | 2026.3+ only |
| Setup required | None | One-time area mapping in device settings |
| Room reference | Name string (`"Kitchen"`) | HA area ID |
| After map retrain | Continues working | Prompts re-mapping |

```yaml
# clean_room — works on any HA version, no setup required
action: roomba_plus.clean_room
target:
  entity_id: vacuum.roomba
data:
  room_name:
    - Kitchen
    - Hallway
  ordered: true   # clean in sequence rather than most-efficient order
  two_pass: true  # optional — overrides the robot's current setting for this job
```

**Individual pass count per room (v2.9.0+):** use `room_passes` instead of `room_name` when different rooms in the same sequence need different two-pass settings:

```yaml
action: roomba_plus.clean_room
target:
  entity_id: vacuum.roomba
data:
  room_passes:
    - name: Kitchen
      two_pass: true
    - name: Hallway   # no two_pass — falls back to the global two_pass field, then the robot's current setting
  ordered: true
```

`room_name` and `room_passes` are mutually exclusive — provide one or the other, not both.

#### Cloud zone selector attributes (Smart Map robots)

`select.{name}_cloud_zone_{map}` (one per floor/map, shown when cloud
credentials are configured) carries per-room metadata as entity
attributes rather than separate entities, the same way `region_icons`
already worked before this table existed:

| Attribute | Type | Notes |
|---|---|---|
| `region_icons` | `dict[str, str]` | Room name → MDI icon |
| `region_areas_m2` | `dict[str, float]` | Room name → floor area in m² *(v2.9.1)*. Computed once from the same UMF geometry used for map rendering — doesn't update on its own; a map retrain reloads the config entry and recomputes it. Present only for whichever floor/map this integration's UMF aligner was built for (the active map at setup) — absent, not zero, on other floors. |
| `learning_percentage` | `int` | Map-learning progress for this floor |
| `region_count` / `zone_count` | `int` | Counts for this map |
| `is_active_map` | `bool` | Whether this is the robot's currently active map |

#### Smart Start with blocking sensor gate

Prevent cleaning from starting when a door is open, a room is occupied, or people are home.

Configure: Settings → Devices & Services → Roomba+ → Configure → **Blocking sensors**

| Option | Values | Default |
|---|---|---|
| Blocking sensors | Any binary sensor entity IDs | (empty) |
| Behavior when blocked | `abort` or `queue and wait` | `queue and wait` |
| Queue timeout | 5–120 min | 30 min |

```yaml
action: roomba_plus.smart_start
target:
  entity_id: vacuum.roomba
data:
  override_blocking: false   # set true to bypass sensors in this call
  rooms:                     # SMART robots only — omit for whole-home clean
    - Kitchen
    - Hallway
```

- **abort** — fires `roomba_plus_start_blocked` event immediately if any sensor is ON
- **queue** — waits until all sensors clear (up to timeout), then starts; fires `roomba_plus_start_timeout` if expired
- Unavailable / unknown sensors are treated as non-blocking

Binary sensor `{name}_start_blocked` — ON while queued, with `blocking_entities`, `queued_since`, `timeout_at` attributes.

#### Zone management

Configure: Settings → Devices & Services → Roomba+ → Configure → **Rooms & zones**

- Browse all zones in a structured index
- Rename any zone; Smart Map robots use the alias alongside the cloud name
- Hide zones — removed from selectors, `clean_room`, and repair issues
- Changes saved atomically

#### Cloud zone sync — Smart Map robots

> ☁️ Requires cloud credentials

- Room and zone names come directly from the Smart Map — no manual naming required

**Where the robot is right now.** A `device_tracker` entity reports the
room or zone being cleaned, and `Docked` when it is on the dock. Zone
names come from a different source than room names — a room is named in
the map's own metadata, while a zone is named by whatever the last
command called it — so a zone you have never cleaned by name may show
its id instead *(v4.0.0a44)*.
- One select entity per floor; multiple Smart Maps supported
- Each saved iRobot app routine appears as a button entity
- `clean_room` uses cloud names directly; map version changes trigger an immediate refresh

#### Room detection — 900-series (v2.10.0)

Automatic room segmentation from the same coverage data used for the heatmap (distance-transform + watershed, the same core technique iRobot's own room-segmentation patent describes), not from travel-gap detection — the previous gap-based approach proved unreliable in the field and has been removed. Rooms and the doorways between them are identified from accumulated visit-density data across missions, with identity kept stable as more missions accumulate so a name you've assigned doesn't reset. New rooms surface via a Repair Issue for naming through the Options Flow; renaming also confirms a room so it appears in `select.{name}_select_zone`.

**Rooms are not derived until three missions have completed** *(v4.0.0a43)*. This technique is measurably unstable on incomplete coverage: a threshold the robot has not yet crossed is indistinguishable from a wall, so it splits a room that is not split, and the split disappears once a later mission drives through. On one real archive that showed as 7 rooms after one mission, 6 after four, and 5 once coverage settled — two of the seven were never rooms. Only *completed* missions count, because a run that ended early has no perimeter pass and its mask edges sit further from the real walls.

**What this can and cannot give you.** A room here is the set of cells the robot drove over, so its shape follows coverage rather than walls: furniture leaves holes, edges sit about one robot radius short of the wall, and boundaries fall at narrow points in the coverage rather than at doorways. More missions fix the *count*; they do not fix the shape, because the sofa is in the same place every time. Wall-accurate outlines would need obstacle data the 900-series does not export.

If you're updating from an earlier version and had already named zones, those names are carried over automatically the first time this version starts up — no action needed.

#### Room type suggestion (v3.2.0, SMART-tier)

Rooms you haven't named yourself (in the iRobot app or via Options Flow) fall back to iRobot's own ML room-type classification instead of a bare region ID — e.g. "Living Room" instead of "19" — wherever ROOM-SIZE or room accessibility scores are shown. Only used when the suggestion's confidence score is clearly positive; a negative score means "probably not this type" and is never shown. A name you've set yourself always takes priority and is never overridden by a suggestion.

#### Map drift detection (v3.1.0, EPHEMERAL-tier)

A Repair Issue (`map_drift_detected`) fires when the robot's recent missions show elevated drift from its expected dock position — tracked over a 10-mission sliding window rather than a lifetime total, so a robot with a long history of normal drift fluctuation doesn't trigger a permanent false positive. The issue clears automatically (with hysteresis to prevent flapping) once drift returns to normal over subsequent missions. Usually indicates the dock was moved or the robot is having difficulty relocating; re-dock and allow a fresh mapping run if it persists.

#### Layout change detection (v3.2.0)

`binary_sensor.*_layout_change_detected` turns on when a spot that was reliably covered for 20+ missions has now been missed for 3 consecutive ones — a sign new furniture or another obstacle may now be blocking it. A companion Repair Issue with the approximate location can be dismissed for 30 days if the change was expected (e.g. a rug added on purpose); the binary sensor itself always reflects the true current state regardless of dismissal, so automations relying on it see reality, not a suppressed notification.

Needs 23 missions of per-cell coverage history before it can judge anything — `cells_tracked` and `missions_until_first_ready` attributes are shown from the very first mission, so you can see it's building history rather than wondering whether it's just quietly broken.

*(v3.4.0: on i/s/j-series robots running lewis firmware (22.52.10+) — which never send local pose data over MQTT — this coverage history is now built from cloud data instead, so this feature populates for those robots too. No setup needed; it activates automatically once map alignment has bootstrapped, typically after a handful of missions.)*

#### Battery / dock contact monitoring (v2.10.0)

A Repair Issue (`battery_contact_suspect`) fires on two independent signals that usually mean a loose or corroded battery/dock contact rather than a failing battery: an implausible jump in reported battery level (more than ~25 percentage points within under 10 minutes — no real battery changes that fast), or the highest battery level reached declining over three consecutive charge cycles. Clean the contacts on the robot and dock before assuming the battery itself needs replacing.

#### Cleaning map

`image.{name}_cleaning_map` — live map rendered as a HA image entity. White background, blue travel path, light-blue cleaned area, dock marker, robot position with direction arrow. Stuck events marked on the map. Map state persists across HA restarts.

**Robots that don't report live position ☁️** — a robot can have persistent Smart Maps and still
never send pose data over local MQTT. The live renderer has nothing to draw from on those, so this
entity would otherwise stay blank white indefinitely, with nothing logged to explain it. (Several
i-series models behave this way — an i3/i3+ on `daredevil` firmware, for instance — but the
fallback keys off what the robot reports about itself, not off any model list.)

With cloud credentials configured, such a robot falls back to the **last completed mission's
coverage as recorded by iRobot's cloud** — the same data the official app draws its post-clean map
from. Nothing changes for a robot that does report position: there, an empty renderer means "this
mission hasn't started yet", which still renders as it always did.

The cloud map is fetched per mission (verified against that mission's own number, so a stale or
mismatched map is never served) and cached, then re-rendered when a newer mission appears. Note
that keep-out and clean-zone overlays are not drawn on the cloud-composited image: those are
positioned in the local renderer's coordinate space, which doesn't apply to a canvas the cloud
compositor scaled to its own extent.

```yaml
type: picture-entity
entity: image.roomba_cleaning_map
show_name: false
show_state: false
```

#### Rooms map — Smart Map robots (v2.3+)

`image.{name}_rooms_map` — static room layout from the UMF floor plan. Available once UmfAligner confidence ≥ 0.70 (typically after 2+ missions with door crossings). On robots without local pose data (lewis firmware 22.52.10+), alignment now bootstraps automatically from cloud traversal events — no action required. Each room renders in its own colour from a rotating 8-colour palette (v2.9.0) for easy visual distinction; rendering uses an embedded font for crisp labels (v2.9.0) and is cached per map version (v2.9.0) so it isn't re-rendered from scratch on every poll.

Both map entities expose `calibration_points` and `rooms` attributes for xiaomi-vacuum-map-card integration. See **[xiaomi-vacuum-map-card.md](xiaomi-vacuum-map-card.md)** for the full setup guide.

**ZONE-OVERLAY + furniture shadows (v3.3.1):** both map entities additionally expose, when aligned:
- `zones` — keep-out zones and robot-observed obstacle zones as raw vector data (`{"type": "keepout", "polygon": [[x,y],...]}` or `{"type": "observed", "x": ..., "y": ...}`, pose-space mm), letting a dashboard draw its own overlay instead of relying on the baked-in PNG rendering described below.
- `door_markers` — inferred door-crossing positions accumulated across missions (`{"id", "cx", "cy", "label", "mission_count"}`, pose-space mm). Known caveat: not re-corrected by drift detection, so a marker can lag slightly behind a large inter-mission drift correction.
- `furniture_candidates` — cells flagged by the FURNITURE detector (reliably covered for a long stretch, now absent) as `{"x_mm", "y_mm"}` pairs, pose-space mm — the same signal that drives `binary_sensor.*_layout_change_detected`, exposed here as a full list rather than first-candidate-only.

All three are withheld in fallback (not-yet-aligned) mode, since the underlying data is pose-space and would be spatially wrong overlaid on a UMF-space fallback render.

The cleaning map overlays keep-out zones (red, semi-transparent) when the UMF aligner is active. **Observed obstacle zones** (v3.0.0) are also overlaid as orange circles — these represent positions where the robot has repeatedly detected obstacles over time, sourced from the UMF `observed_zones` data.

The native **Roomba+ platform was merged into xiaomi-vacuum-map-card in v2.4.1** (June 2026). On that version or newer, pick **Roomba+** as the `vacuum_platform` in the card editor and use the **"Generate Room Configs"** button — it reads the `rooms` attribute and builds the room overlay for you, no manual coordinates:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
vacuum_platform: roomba_plus        # XVMC v2.4.1+; then use "Generate Room Configs"
map_source:
  camera: image.roomba_rooms_map    # or image.roomba_cleaning_map
calibration_source:
  camera: true                      # reads calibration_points automatically
```

On older XVMC versions (no Roomba+ platform in the dropdown), define `map_modes` with explicit `predefined_selections` manually — the full fallback example is in [xiaomi-vacuum-map-card.md](xiaomi-vacuum-map-card.md).

#### Coverage map (v2.2+)

`image.{name}_coverage_map` — EMA-weighted occupancy heatmap, updated at each mission end.

---

### Room cleaning setup — HA areas (`vacuum.clean_area`, HA 2026.3+)

`vacuum.clean_area` is a native HA action that lets you trigger room cleaning using HA areas (the same areas used for lights, climate, and other devices). It is the recommended approach on HA 2026.3 or newer. On older HA, use `roomba_plus.clean_room` instead — it works identically without any setup.

**Prerequisites:** SMART robot · cloud credentials configured · HA 2026.3+

### One-time setup

1. HA will raise a Repair notification: **"Map vacuum segments to areas"** — this is expected
2. Open the vacuum entity → ⚙ Entity settings → **"Map vacuum segments to areas"**
3. Match each robot room to a Home Assistant area
4. Save

The left side of the dialog lists your **iRobot room names** (pulled from the cloud — Roomba+ exposes them as HA "segments" with their real names, grouped by floor if you set a floor label in Configure). The right side is your HA areas. You assign them yourself: the mapping is intentionally manual and lives in HA, not in the integration. There is no reliable automatic match — robot room names and HA area names differ by language, spelling, and how each was set up, and a wrong auto-mapping (cleaning the wrong room) would be worse than none. Doing it once takes under a minute.

If the robot retrains its map later, HA raises the Repair again to re-confirm the mapping.

### Using the action

```yaml
action: vacuum.clean_area
target:
  entity_id: vacuum.roomba
data:
  cleaning_area_id:
    - living_room    # HA area ID (not the room name string)
    - kitchen
```

`roomba_plus.clean_room` (by room name) and `vacuum.clean_area` (by HA area) are fully interchangeable for SMART robots on HA 2026.3+.

---

## Maintenance & health

#### Remaining life sensors

| Sensor | Notes |
|---|---|
| Filter remaining hours | Configurable threshold; `threshold_hours` attribute |
| Brush remaining hours | Configurable threshold; `threshold_hours` attribute. Covers main and side brushes together as a single maintenance action — the local protocol doesn't report separate wear signals for each, so there's no reliable way to split this into two sensors. If your official app shows them as separate checklist items, treat this one sensor as covering both. |
| Cleaning pad remaining hours | Braava only |
| Battery capacity retention (%) | Degradation relative to design capacity (profile-corrected, v2.5+) |
| Estimated battery end of life (days) | Projected days until battery replacement — self-calibrated against this robot's own measurement noise floor (v3.1.0), so a near-new battery with normal estCap jitter no longer produces a meaningless multi-decade projection |

**Self-calibrating thresholds (v2.5+):** After two or more filter or brush replacements, Roomba+ learns your personal replacement interval from the actual hours between resets. The learned value is visible in diagnostics under `learned_maintenance`.

**First install on an already-used robot:** if this is the first time Roomba+ has seen this robot and it already has significant runtime hours (e.g. installed after months of use via the official app), the remaining-hours countdown assumes maintenance is current as of install time rather than treating the robot's entire prior lifetime as "overdue" — you won't see a false "0h remaining" the moment you add the integration. The countdown then behaves normally from that point on; press the reset buttons whenever you actually replace something to keep it accurate. **With cloud credentials configured this assumption is replaced by the real thing** — see cloud consumable counters below.

#### Cloud-synced consumables ☁️

> ☁️ Requires cloud credentials. Applies to whichever consumables your robot's account reports.

iRobot's cloud tracks each consumable itself, in robot runtime minutes, and that is what the
official app's maintenance tiles show. Where a Roomba+ sensor already exists for a part, it now
reads that counter instead of estimating locally:

| Sensor | Source when cloud is available |
|---|---|
| Filter remaining hours | iRobot's own filter counter |
| Brush remaining hours | iRobot's own **main** brush counter |

Robots or accounts that don't report parts keep the previous behaviour exactly — the local
threshold estimate, hydrated baseline included — so nothing regresses for them.

Consumables with no local wear signal get their own sensors, created only when the account
actually reports that part (so a Braava gets no edge-brush sensor, and a robot without a Clean
Base gets no bag sensor):

| Sensor | Notes |
|---|---|
| Edge sweeping brush | The side brush. Wears on its own schedule, so it is not averaged into the main brush sensor |
| Dust Bag | The Clean Base bag |

**Migrating from the official app:** a part's cloud counter includes replacements you confirmed
in iRobot's app, so the runtime-hours reading at each replacement is recoverable, and Roomba+
adopts it as its baseline on the first cloud refresh. That replaces the install-time "assume
everything is fresh" assumption with your robot's real service history, which is also what gives
the `*_days_until_due` sensors something real to extrapolate from. Your own confirmed resets keep
building the self-calibrating history from there; a hydrated baseline is not counted as a
replacement event and does not skew the learned interval.

**Resets go both ways.** Pressing a reset button or calling `roomba_plus.reset_filter` /
`reset_brush` now also records the replacement with iRobot, so the app and Home Assistant agree
about a part you just changed. This is best-effort: the local reset is saved first, and a cloud
failure is logged without failing the action. The edge brush and bag have their own reset buttons,
which write to the cloud counter directly since they have no local slot.

**About part identification:** the endpoint returns a numeric `part_id` and no name — the app
resolves names from a per-SKU catalog it does not serve, and the id space is not the same across
robot families. `IROBOT_PART_ROLES` in `const.py` records the mapping and the evidence for each
entry; an id this integration doesn't recognise is left unmapped rather than given an invented
name, following the same reasoning as the Prime path's `_KNOWN_PARTS`.

#### Status

`sensor.{name}_status` reports what the robot is doing, translated into
your Home Assistant language. Both robot generations report the same set
of states *(v4.0.0a46)* — the wording differs from what the robot itself
sends, and automations should match the state value rather than the
displayed text.

Two states are worth knowing about:

- **Charging mid-mission** — the robot returned to top up and will
  resume. Separate from plain charging, because an automation reacting to
  charging would otherwise fire mid-clean.
- **No contact** — nothing has been received for over an hour. Every
  other entity is still showing its last value, which may be days old.

#### Clean Base / dock status

`sensor.{name}_clean_base_status` — the dock's own health, not the robot's onboard bin: tank/bag missing, low, clogged, a sealing problem, a full bag, an IR communication issue, or ready/empty. This is the entity that corresponds to what the official app calls the "docking station bag" indicator — distinct from `binary_sensor.{name}_bin_full`, which reflects the robot's own onboard dust bin (rarely full on a Clean-Base-equipped setup, since the robot empties into the dock automatically after each mission). Requires a Clean Base — absent otherwise.

#### Navigation health (v3.1.0, SMART-tier)

> Disabled by default · i/s-series (lewis firmware)

`sensor.{name}_relocalisation_rate` — tracks how often the robot needs to relocalise during a mission, a direct signal of Smart Map quality. Self-calibrates against the robot's own normal rate over its first 15 missions, then compares the recent 10-mission window against that personal baseline. A Repair Issue fires at 3× baseline and clears automatically once the rate normalises.

#### Replacement tracking

| Sensor | Button / Action | Robots |
|---|---|---|
| `filter_last_replaced` | `reset_filter` | All |
| `brush_last_replaced` | `reset_brush` | Vacuums |
| `pad_last_replaced` | `reset_pad` | Braava |
| `battery_last_replaced` | `reset_battery` | All |
| `wheel_last_cleaned` | `reset_wheel_cleaning` | All (v2.7+) |
| `contact_last_cleaned` | `reset_contact_cleaning` | All (v2.7+) |
| `bin_last_cleaned` | `reset_bin_cleaning` | All (v2.7+) |

**Calendar-based inspect tracking (v2.7+):** wheel module, charging contacts, and bin are cleaned on a calendar cadence rather than hours-of-use. Three new timestamp sensors and services track when each was last cleaned so you can build reminders from them.

**Reset learned profile:** `roomba_plus.reset_robot_profile` wipes all self-calibrated baselines (dirt thresholds, maintenance intervals, coverage baseline) so the robot starts learning fresh after a move or major layout change. Mission history is unaffected.

Every reset above (button or service) writes a searchable Logbook entry and fires `roomba_plus_maintenance_reset` — see [Events & device triggers](#events--device-triggers).

#### Maintenance due binary sensor

`binary_sensor.{name}_maintenance_due` — ON when any consumable reaches zero remaining hours. Attributes: `due` (list of consumables), `overdue_by_hours` (hours past threshold per consumable). Home Assistant's default dashboard tile for a `problem`-class binary sensor shows only on/off, not attributes — click into the entity's more-info dialog and check "Attributes" (or view it in Developer Tools → States) to see which consumable is actually due. Also available as the `maintenance_due` device trigger. If left unaddressed for 3+ days, also raises a Repair Issue — a backstop for anyone without an automation wired to the trigger.

#### Maintenance to-do list (v3.4.0)

`todo.{name}_maintenance` — filter replacement and brush/pad cleaning as real Home Assistant to-do items, always present. Due date comes from the same self-calibrated wear-rate estimate as the `*_days_until_due` sensors (absent until a wear rate is established — early in a robot's life, or right after a reset). Marking an item done fires the same reset as the corresponding button (`reset_filter` / `reset_brush` or `reset_pad` on Braava) — same Logbook entry and `roomba_plus_maintenance_reset` event either way.

SMART-tier robots (i/s/j-series, Braava) also get a **Reconfigure rooms** item whenever a Smart Map zone has no assigned name yet — same condition as the zone-naming Repair Issue. This item isn't manually completable: it disappears on its own once every zone is named via the existing naming wizard; marking it done by hand has no effect and it simply reappears on the next update if unnamed zones remain.

#### Dock contact health (v2.8.0)

> i/s-series (lewis/soho firmware); some 9-series variants

Monitors three dock-contact counters (`nChatters`, `nKnockoffs`, `nAborts`) and raises a **Repair Issue** when any exceeds its threshold — an early signal of dock-contact wear, separate from the SMBus battery-communication check (`smberr`). Auto-resolves once counters drop back below threshold.

#### Wear Intelligence

| Sensor | Notes |
|---|---|
| Filter / brush wear rate (h/day) | Recalculated after each reset |
| Filter / brush days until due | Projected days at current wear rate |
| Pad wear rate / days until due | Braava only |

> Wear sensors show `Unknown` for the first 3 days after a reset.

#### Device diagnostics (opt-in)

Battery capacity (mAh) · Navigation panic events · Cliff events front / rear · Navigation landmark quality (9-series) · Optical / piezo dirt detections, navigation orientations (i/s-series) · Dock contact chatters / knockoffs / charge aborts (v2.8.0) · Wi-Fi last channel, channel stability, missions per charge (v2.8.0)

#### Health score trend (v3.2.0)

`sensor.*_health_score_trend` classifies the recent trend in the existing integration health score as `improving`, `stable`, or `declining` — self-calibrated against this specific robot's own learned baseline and noise level, not a fixed point threshold. Needs 44 days of history before it can judge (30-day reference period + 14-day comparison window); a Repair Issue fires once a decline has persisted 14+ days and auto-resolves once it recovers. A `days_until_ready` attribute counts down the wait so it's not just an opaque `Unknown` in the meantime.

#### Reset diagnostics (v3.2.0)

`sensor.*_reset_diagnostics` (diagnostic) exposes the robot's own reset-cause breakdown (`bbrstinfo`) that was previously entirely unread: navigation resets, mobility resets, and safety-triggered resets (the native value — the most actionable single counter) as extra attributes, plus out-of-memory resets on firmware that reports them. Deliberately a plain diagnostic sensor, not folded into the integration health score.

#### Cleaning cadence health (v3.2.0)

Learns each room's own typical interval between cleans from its cleaning history, then flags rooms that have gone noticeably longer than their own normal rhythm without being cleaned — self-calibrated per room, not a fixed schedule. Exposed via `format=zone_coverage_health` on the [REST history API](API.md#format=zone_coverage_health) and a per-room Repair Issue when overdue.

---

## Mission history & room intelligence

> **A gap in the history is not a failure.** A robot that is stuck or in
> an error state does not attempt its scheduled missions, and iRobot
> records nothing for an attempt that never happened — so "no mission
> recorded" and "no mission attempted" look identical afterwards.
>
> @utkjmitch captured this across a 48-hour stall: two scheduled runs
> passed with no attempt, no failure and nothing in the history. Every
> entity was correct throughout — `phase` read `stuck` accurately for
> the whole period — but nothing in the mission history says why those
> two days are empty.
>
> Schedules run on the robot and in iRobot's cloud, not here, so this is
> vendor behaviour rather than something the integration can change.
> Worth knowing before reading a gap as a fault.

#### Room rhythms & mission maps (v3.3.0)

- **`sensor.*_rooms_overdue`** (SMART + cloud) — which rooms are due for a clean. Each room's rhythm is learned from its own history; set an explicit frequency per room in the options flow (Daily / Every 2 days / 3× per week / Weekly) to override the learned interval. Attributes include a fully self-calibrated suggested interval per room and a `daily_suggested` list for rooms that re-dirty fast.
- **`roomba_plus.clean_overdue_rooms`** — one service call cleans everything that's due, worst first, with the route between rooms travel-optimized from the dock. Does nothing when nothing is due — safe to fire daily from an automation.
- **Mission cleaning maps** — every finished mission's real coverage, as an image URL (`…/missions/latest/map.png`) for picture cards and notifications, or as raw coordinates (`map.json`). See [the API docs](API.md#get-missionsrecord_idmapjson----mappng).
- **Dirt ↔ sensor correlation** (opt-in) — pick any HA sensors (humidity, pollen, …) in the settings; after 30 missions the integration reports whether your robot verifiably collects more dirt when they're high, entirely locally.

#### Mission log

Every mission is recorded to a persistent log (up to 365 entries, FIFO). Survives HA restarts.

| Sensor | Notes |
|---|---|
| Clean streak | Consecutive days with at least one completed mission |
| Missions last 30 days | Count of completed missions |
| Completion rate (30 days) | Completed ÷ total × 100 |
| Area cleaned today | Sum of mission area today (VSLAM robots) |
| Last mission result | `completed` / `stuck` / `cancelled` / `error` / `demand` |
| Last mission duration | Duration in minutes |
| Last mission summary | Most recent mission as a single entity — 14 attributes (duration, area, battery delta, recharges, dirt events, initiator, timestamps) for automation triggers without digging through history (v3.1.0) |
| Room cleaning history | Dictionary sensor: `{room_name: last_cleaned_timestamp}` across all recorded missions, SMART-tier with cloud access (v3.1.0) |
| Consecutive anomalous missions | Count of consecutive most-recent missions classified as anomalous (v3.0.0, disabled by default — threshold ≥ 3 triggers the Card C5-ANOMALY banner) |
| Last mission team ID | `team_id` of the most recent mission, if part of an Imprint Link team clean — `null` for the vast majority of ordinary single-robot runs (v3.2.0, disabled by default) |

Every mission also writes a searchable Logbook entry and fires `roomba_plus_mission_completed` — see [Events & device triggers](#events--device-triggers).

A multi-room mission waits briefly (typically up to ~90 seconds, occasionally less since v2.10.1) after the robot reports being done before recording it — this is deliberate, to avoid mistaking a pause between rooms for the mission finishing early.

#### Mission progress (v2.6+)

> ☁️ Requires cloud credentials · SMART robots only

`sensor.{name}_mission_progress` — live mission completion percentage (0–100 %) using per-room time estimates and effective mission time (wall-clock duration minus robot-confirmed recharge time — see below). The timer persists across HA restarts.

Attributes: `current_room` · `next_room` · `elapsed_run_min` · `estimated_remaining_min` · `room_sequence` · `mission_duration_min` *(v2.9.0)* · `recharge_min` *(v2.9.0)*

**Time tracking (v2.9.0):** `elapsed_run_min` is now `mission_duration_min` (pure wall-clock time since mission start) minus `recharge_min` (robot-confirmed mid-mission recharge time) — no more fixed time-based cutoff. Navigation and room-to-room transitions correctly count as active mission time even when they take several minutes; only confirmed recharging is excluded. `mission_duration_min` and `recharge_min` are also exposed directly so you can see the breakdown. In **Auto pass mode**, per-room cloud estimates aren't available at all — percentage and `estimated_remaining_min` now fall back to your robot's rolling average mission duration instead of staying "Unknown" for the whole mission.

**Automatic room advancement (v2.8.0):** on lewis-firmware robots (i7+/s9+), the progress sensor can occasionally get stuck reporting a completed room as still in progress. Roomba+ now detects room transitions automatically using your robot's real per-room cloud time estimates — no setup required. If it ever needs a manual nudge, use the `roomba_plus.advance_room` action.

#### Mission phase sensors

| Sensor | Notes |
|---|---|
| `mission_elapsed_min` | Minutes elapsed in the current mission |
| `mission_recharge_minutes` | Countdown until robot resumes after mid-mission dock |
| `mission_expire_minutes` | Countdown until the mission expires |
| `binary_sensor.mission_active` | ON for the entire mission arc including mid-mission recharge |
| `binary_sensor.mid_mission_recharge` | ON only during the mid-mission charge phase |

#### Room intelligence — Smart Map robots

> ☁️ Requires cloud credentials

| Attribute | When | Notes |
|---|---|---|
| `planned_room_order` | **During mission** | Rooms in requested order; populated at mission start |
| `mission_destination` | **During mission** | Last room in `planned_room_order` |
| `last_cleaned_rooms` | **Post-mission** | Rooms confirmed cleaned |
| `room_coverage` | **Post-mission** | Per-room cleaned fraction (0.0–1.0) |

`sensor.{name}_room_areas` (v3.1.0) — dictionary sensor: `{room_name: floor_area_m2}`, calculated from UMF polygon data. The only automatically-measured room area available without a tape measure.

`sensor.{name}_room_accessibility_scores` (v3.2.0) — dictionary sensor: `{room_name: {score, limiting_factor}}`, a 0–100 score combining stuck-event rate, coverage gap, and time-per-area — each judged against this robot's own average across its other rooms, not a fixed threshold. `limiting_factor` names which signal (`obstacle_density` / `coverage_gap` / `narrow_passages`) is pulling the score down. A per-room Repair Issue fires below 60.

#### Error intelligence

| Sensor | Notes |
|---|---|
| `last_error_code` | From live MQTT or persisted MissionStore value; `description` + `action` attributes |
| `last_error_at` | Timestamp of the last error or stuck event |
| `last_error_zone` | Zone where the error occurred |
| `stuck_count_30d` | Stuck events in the last 30 days |
| `problem_zone` | Most frequently stuck zone over 30 days (VSLAM robots) |

#### Mission anomaly detection (v2.5+)

Monitors each mission against the last 30 days of your robot's personal history. A Repair Issue is raised after two consecutive statistically unusual missions. Activates after 20 missions of history and clears automatically once missions return to normal.

> **v2.8.0:** if you have cloud credentials and prior mission history in the iRobot app, the anomaly baseline and per-room dirt index are bootstrapped from that full cloud history at first setup — both features can activate within days instead of needing several weeks of fresh local history.

#### Anomaly explanation (v3.2.0)

The `roomba_plus.explain_mission` service (and its [REST equivalent](API.md#get-missionmission_idexplain)) turns a flagged anomaly into a plain-language reason: `obstacle_or_blockage`, `excessive_recharge`, `dirt_spike`, or `incomplete_coverage`, plus a matching recommended action. `robot_lifted` (the robot was picked up during the mission) and `error_code` are reported alongside, independently — a mission can have either regardless of which anomaly reason applies, if any.

The same explanation is also included directly in every `roomba_plus_mission_completed` event payload (see [Event bus](#event-bus-v286)) — a notification automation gets `anomaly_reason` and `recommended_action` for free, without calling the service.

#### Stuck pattern time-correlation (v2.7+)

Tracks the weekday and hour of every stuck event per grid cell. When a cell accumulates ≥ 8 stucks with more than 60 % concentrated in the same time slot, a **Repair Issue** is raised: *"Your Roomba gets stuck near Kitchen most often on Tuesday mornings."* Auto-clears when the pattern changes. Existing stuck data from v2.6.x migrates automatically.

#### Stuck hotspot clusters (v3.2.0)

Groups adjacent stuck-event grid cells (two or more neighbouring cells independently qualifying is itself a strong signal — physically near-impossible to be coincidence at the 150mm cell size involved) into a Repair Issue with compass bearing, room name (Smart Map robots), and cell count. Since a robot's lifetime stuck count never decreases, resolution is judged by whether the cluster's coverage has recovered relative to its surroundings, not by the stuck count itself — a permanent stuck-history doesn't keep the issue open forever once the actual obstacle is gone.

*(v3.4.0: on lewis-firmware robots (see the note under Layout change detection above), this also sources from cloud data — structurally wired up and tested, but whether it populates for a real stuck incident on lewis firmware specifically hasn't been field-confirmed yet. Everything else this release brings to lewis-firmware robots — the coverage heatmap and layout-change detection above — doesn't depend on this and works regardless.)*

#### Performance sensors

> ☁️ Requires cloud credentials

**Consolidated analytics sensors (v2.7+, enabled by default):**

| Sensor | State | Key attributes |
|---|---|---|
| `sensor.{name}_cleaning_performance` | Completion rate (%) | speed, trend, coverage_pct, clean_streak |
| `sensor.{name}_cleaning_analytics_30d` | Cleaned area (m²) | time_h, dirt_density, recharge_pct |
| `sensor.{name}_wifi_health` | Signal floor (%) | stability_pct |
| `sensor.{name}_event_counts_30d` | Last error code | recharges, evacuations, dirt_events, error_time |

The 15 individual `recent_*` sensors (e.g. `recent_cleaning_speed`, `recent_dirt_density`) are now **disabled by default** on fresh installs. They were permanently removed in v3.0.0. A one-time warning is logged when they are read to guide migration.

**Robot health score (v2.7+):**

`sensor.{name}_robot_health_score` — composite 0–100 score combining battery retention, navigation efficiency, cleaning speed trend, anomaly rate, and stuck rate. Visible in the main sensor list (not diagnostic). Shows Unknown until 20 missions of history have accumulated. Carries `status_text` and `recommendation` attributes (v3.1.0, all 8 languages as of v3.4.2) for a plain-language summary alongside the numeric score — `integration_health` has the same attributes.

#### Map intelligence — Smart Map robots (v2.6+)

> ☁️ Requires cloud credentials

| Sensor | Notes |
|---|---|
| `sensor.{name}_map_learning` | Map completeness score (0–100 %) from the iRobot cloud |
| `sensor.{name}_zone_summary` | Clean zone count; `keepout_zones`, `observed_zones` attributes |

#### HA Long-Term Statistics

Roomba+ backfills up to 365 days of mission history into HA Long-Term Statistics on every startup. Add a Statistics graph card and search for `roomba_plus:` — three series: area cleaned, mission duration, missions completed.

#### REST history API

```
GET /api/roomba_plus/{entry_id}/mission_history?format=summary|records|hazards|export|zone_coverage_health
POST /api/roomba_plus/{entry_id}/mission_history/import
GET /api/roomba_plus/{entry_id}/digest?date=YYYY-MM-DD
GET /api/roomba_plus/household
GET /api/roomba_plus/{entry_id}/mission/{mission_id}/explain
GET /api/roomba_plus/{entry_id}/mission/{n_mssn}/path
```

The `.../mission/{n_mssn}/path` endpoint (v3.2.0) reconstructs a mission's room-by-room timeline — "Kitchen at 09:05, Hallway at 09:23, Bedroom at 09:31" — room-granular, not pixel-accurate pose tracking.

> Full parameter reference, response shapes, and curl examples: **[docs/API.md](API.md)**

---

## Presence & scheduling

#### Cleaning schedule calendar (v3.4.0)

`calendar.{name}_schedule` — your robot's cleaning schedule (`cleanSchedule2` on i/s/j-series, legacy `cleanSchedule` on 900/600-series) as recurring Home Assistant calendar events. Read-only, always created on every tier — an empty calendar just means no schedule is currently set. Each event uses a fixed 60-minute placeholder duration, since iRobot's schedule data carries a start time only, never a planned duration.

Room names in schedule summaries are resolved from **every map on the account**, not just the one
currently drawn — a schedule that spans maps used to get half its rooms named and half numbered as
`Zone N`.

**Prime robots can write it, and three services do that directly** — the calendar entity is the
usual way in, but a script may prefer the service:

| Service | What it does |
|---|---|
| `roomba_plus.create_schedule` | adds a schedule: days, time, and optionally which rooms |
| `roomba_plus.update_schedule` | changes an existing one, identified by its schedule id |
| `roomba_plus.delete_schedule` | removes one |

**An update replaces the whole schedule**, so anything you leave out keeps its current value rather
than being cleared — the integration reads the schedule first and changes only what you named. That
is not politeness: the cloud API has no partial update, and a naive write would silently drop the
days you did not mention.

Editing through the calendar dialog goes down the same path. Home Assistant sends only the fields
the user touched, so a time change arrives with no recurrence information at all — which is why both
the frequency and the day list are preserved rather than rebuilt from the occurrence that was
clicked.

#### Presence-aware scheduling — i/s/j/Braava

Automatically unfreeze the cleaning schedule when everyone leaves home.

Configure: Settings → Configure → **Presence-aware scheduling**

| Option | Description | Default |
|---|---|---|
| Enable | Master toggle | Off |
| Tracked persons | One or more `person.*` entities | — |
| Mode | `Unfreeze when all away` or `Fire event (manual control)` | Unfreeze when all away |
| Delay after leaving | Minutes to wait before unfreezing (0–60) | 5 min |

The manager only re-freezes a hold it created — never interferes with a hold set manually via the Schedule Hold switch.

Events: `roomba_plus_all_away` · `roomba_plus_person_detected_during_clean`

`sensor.{name}_optimal_clean_window` — best hour to clean today, derived from historical away patterns. Attributes include `window_is_today: bool` (v2.6) so automations can distinguish "best window is today" from "best window is tomorrow".

#### Demand cleaning — SMART robots with cloud (v2.4+)

> ☁️ Requires cloud credentials

Automatically trigger an unscheduled clean when the floor is significantly dirtier than usual.

Configure: Settings → Configure → **Demand cleaning**

| Option | Default | Description |
|---|---|---|
| Enable demand cleaning | Off | Master toggle |
| Trigger multiplier | 1.5 | Fire when dirt density > baseline × multiplier |

After each mission, dirt density is compared against the weekday-aware baseline (v2.5+) or 30-day flat median. All gates must pass before a trigger fires: robot idle, no blocking sensor active, all tracked persons away (if presence mode is `away_only`), minimum 6 h since last demand trigger.

`binary_sensor.{name}_demand_clean_blocked` — ON when any gate is active. The `blocking_reason` attribute (v2.6) names the specific gate.

#### Presence analytics

| Sensor | Notes |
|---|---|
| Clean opportunities (7 days) | Away windows long enough for a full clean |
| Clean utilisation (7 days) | % of those windows that resulted in a clean |
| Next likely clean window | Heuristic forecast |
| Optimal clean window | Best hour today; `window_is_today` attribute (v2.6) |

---

#### Mission progress is an estimate, not coverage

`sensor.{name}_mission_progress` has two modes, and neither measures how much
floor has been cleaned:

- **With a planned room order** — rooms completed against rooms planned
- **Without one** — elapsed time against this robot's rolling mean duration

The second is the common case on Prime, and it is convincing: it climbs at a
steady rate and reads exactly like measured progress. It is arithmetic on the
clock.

**It never reaches 100.** "Finished" is a phase transition, not a percentage,
so the sensor caps at 99 and the mission ending is what clears it. If the phase
gets stuck — which can happen cloud-side, see below — it parks at 99
indefinitely.

`sensor.{name}_area_cleaned_today` is the coverage figure, and on some robots
it stays at zero while progress climbs. The two are not measuring the same
thing.

The unit follows **your Home Assistant unit system** — square feet on an
imperial setup, square metres on a metric one. The iRobot account's own
"Metric Units" switch has no effect: it is not in the data the robot
sends, and an integration should not override a Home Assistant
preference from a vendor account setting.

#### Favourites are per robot

Your iRobot account's favourites are shared across every robot on it, but each
favourite names the robot it was built for. **Only that robot gets a button.**

A favourite that names no robot is shown under all of them — better visible
everywhere than hidden from all.

If you have several Prime robots and a button disappeared after upgrading, it
belonged to a different one. Pressing it would have sent that robot's map
regions to this one.

#### Pad washing allowed (V4/Prime)

`switch.{name}_pad_wash_allowed` turns pad washing on and off, gated on the
dock's wash capability. Its sibling — pad **drying** allowed — has been there
since a20; this one was withheld until iRobot's newer app showed it is written
on its own rather than as part of a bundle.

#### Pad wash interval: what the numbers mean

`select.{name}_pad_wash_area_interval` carries bare numbers — 6, 8, 10, 15, 20 —
and the unit is **tens of square feet**:

| Value | Robot cleans before washing |
|---|---|
| 6 | 60 ft² ≈ 5 m² |
| 8 | 80 ft² ≈ 7 m² |
| 10 | 100 ft² ≈ 9 m² |
| 15 | 150 ft² ≈ 14 m² |
| 20 | 200 ft² ≈ 19 m² |

**Smaller means more frequent washing**, which is the opposite of how it reads.
The iRobot app hides this behind "High / Medium / Low" and shows the area in
your own units — @chairstacker sees "after every 100 ft²" for value 10, while
@ratpic83 sees "5 / 10 / 15 m²" for 6, 10 and 15.

The numbers are kept here rather than translated to High/Medium/Low for two
reasons: **the sets differ per robot series**, so the same word would mean a
different area on different hardware, and a robot can report a value outside
its own set — which has no position to name.

#### Pad wetness (V4/Prime, a35)

`select.{name}_pad_wetness` writes `padWetness.padPlate`, gated on
`cap.ppWetLvl`. A robot reporting `0` there has no wetness control and gets no
entity — which is a real difference between two robots of the same model with
different docks.

**The level range is the one value set in this integration with no documented
source.** The key, its addressing and its gate are all confirmed; the range is
in no vendor enum, no settings-key type and no locale string. Four is the
highest anyone has seen, and the picker widens itself to include whatever your
robot reports, so a guessed ceiling cannot hide a real setting.

#### The mop tank sensor is unreliable on some Combos

`binary_sensor.{name}_mop_tank_present` reads the robot's own `tankPresent`
field, and on at least one Roomba Combo **that field describes nothing**.

@chairstacker's 405 reports `tankPresent: true` while the robot has no fill
port, the iRobot app shows no water level for the robot at all, and the value
does not move when either dock tank is removed. On that hardware the pad is
wetted at the dock and there is no onboard tank to be present.

**It is kept rather than removed**, because nothing distinguishes a robot with
a tank from one without: there is no capability flag for it, and `cap.ppWetLvl`
describes pad wetness the dock applies. Withholding it everywhere on one
robot's evidence would take a working reading away from any robot where the
field is real.

**If yours reads `off` when you pull the tank, it works and this note does not
apply to you** — and that observation is worth reporting, because nobody has
made it yet.

#### Per-room preferences (V4/Prime)

`image.{name}_rooms_map` carries a `room_preferences` attribute: what each room
is already set to in the iRobot app.

```
{ "7": {"profile": "deep", "suction_level": 4, "two_pass": true} }
```

Keys are room ids. Each value carries only what that room reports — `profile`,
`suction_level`, `two_pass`, `carpet_boost`, `scrub` — and **an absent key means
the robot did not report it, which is not the same as a zero.**

The settings are read under the mode the room last ran in, because
`operating_mode_defaults` stores one set per mode and reading any other would
report a setting for a mode the room is not in.

Use it to honour a room's own configuration instead of overriding it — see
[Automations](AUTOMATIONS.md#cleaning-a-room-the-way-it-is-set-up).

**Not confirmed on any robot.** A diagnostics download from a36 onwards
includes it, and says which of two reasons applies when it is empty.

#### Settings showing an unusual value

A control whose current value is not in the list your robot's series normally
offers now **shows that value** rather than going unavailable.

@DaRealGuGu's 515 holds a pad wash interval belonging to a different series,
and the iRobot app renders it as "not set". Selecting it again is a no-op — it
is the value the robot already has.

#### Started by, on Prime (a35)

`sensor.{name}_job_initiator` says who started the current or most recent
mission — schedule, iRobot app, dock button, Alexa, Siri, IFTTT, a teamed
robot, or Home Assistant.

The **last command** is carried as attributes rather than as a second entity:
`last_command`, `last_command_by`, `last_command_time`. It is a different
question, and the two can legitimately disagree — a mission started by a
schedule while the last command came from the app.

#### The third phase category (a35)

The phase sensor carries two attributes:

```
cleaning    true during run, hmMidMsn, evac
dock_task   true during padWash, padDry, refill
```

They are never both true. A combo robot with `pwReturn: 2` washes its pad
**during** cleaning, and the vendor's own rule says that is not a mission
phase — it is a dock task. An automation gating on "is it cleaning" will go
false while the robot is plainly working, and `dock_task` says why.

#### Do Not Disturb (V4/Prime, a34)

Quiet hours are two mechanisms plus a display, and the robot's protocol keeps
them separate:

| | |
|---|---|
| `roomba_plus.set_quiet_hours` | writes the daily window, or a one-off "quiet until" moment |
| `switch.{name}_do_not_disturb` | turns DND on and off right now |
| `binary_sensor.{name}_in_quiet_hours` | whether a scheduled window covers this moment |

The action takes **either** a daily window (`start` and `end`) **or** a single
`ends_at` — the robot's own format is a sealed type with exactly those two
cases, and sending both returns an error. It is household-wide: every robot on
the account is affected, and the entity you target is only how the household is
found.

The switch and the sensor answer different questions and can legitimately
disagree — DND switched on while no scheduled window covers right now. The
switch has no shadow field to read back from, so its state is what it last
sent and restores across a restart; a change made in the iRobot app will not
show up there.

**Its effect is unproven.** A Prime robot has been observed cleaning inside its
own quiet-hours window. The setting is accepted and reads back; whether the
robot honours it is a separate question no field report has answered yes. Keep
automations that depend on quiet hours in Home Assistant rather than relying on
the robot.

#### Start blocked (V4/Prime, a34)

`binary_sensor.{name}_start_blocked` turns on when one of four faults would
stop a mission from starting, and says what still works:

```
blocked_modes    ["vacuum"]
available_modes  ["mop"]
blocking_faults  [287]
blocked_reason   "Unable to vacuum: remove Pad Plate"
```

Three of the four do not mean the robot is broken — they mean one half of what
you would ask is impossible right now. `287` is the pad plate fitted (mop
works), `290` the plate missing (vacuum works), `234` the plate fitted with no
cloth on it (vacuum works), `286` the robot off the floor (neither).

**Nothing is refused.** A vacuum command against `287` still goes out; what
changes is that the robot's refusal is explainable in advance. A device
trigger, *Start blocked by a fault*, fires when a block appears.

---

## Connectivity, mop & configuration reference

#### Braava / mop sensors

Tank level · Mop pad type · Mop clean mode · Mop tank status (`Ready` / `Fill Tank` / `Lid Open` / `Tank Missing`) · Mop ready / tank present / lid closed binary sensors

Braava pad wetness control: select wetness level (Low / Medium / High) independently for disposable and reusable pads.

#### Configuration reference

Settings → Devices → Roomba+ → Configure

**Connection settings:**

| Parameter | Default | Description |
|---|---|---|
| Continuous connection | `true` | Keep MQTT connection open permanently |
| Connection delay (s) | `30` | Seconds between reconnect attempts |
| Map enabled | `true` | Enable live map rendering (900-series) |
| Map size (px) | `600` | Rendered map image size (400–1200) |
| Map scale (mm/px) | `10.0` | Millimetres per pixel |

**Options menu structure (v2.6):**

| Section | Steps |
|---|---|
| ⚙ Connection | Settings · iRobot cloud credentials |
| 🗓 Scheduling | Blocking sensors · Presence-aware scheduling · Demand cleaning |
| 🗺 Map | Zone management · Rooms & zones |

*Smart Map robots also show a conditional* **Import rooms from roomba_rest980** *entry (v2.9.0+) when an existing roomba_rest980 installation is detected — see [Migration](../README.md#migration).*

#### Diagnostics download

Settings → device → ⋮ → Download diagnostics. Includes map subsystem, zone subsystem, geometry subsystem, cloud subsystem, `robot_profile` (v2.5+: confirmed profile, chemistry, BMS scale factors), `learned_maintenance` (v2.5+: learned filter and brush lifespan hours), and `sub_module_sw_versions` (v2.8.0: per-component firmware build hashes — useful for spotting differences between otherwise-identical firmware versions).

---

## The words on your screen come from iRobot

This integration wrote its own error labels for years. A comparison against the vendor's own app
found that **two of 126 matched** — and the differences were not stylistic:

| ours | iRobot |
|---|---|
| Charging error | Charging Issue: unable to charge |
| No charge current | Charging Issue: contacts need to be cleaned |
| Right wheel error | Right wheel sensor issue |

The second column tells somebody what to do. The first tells them something is wrong, which they
already knew from the robot stopping.

**112 codes now carry iRobot's own title and explanation**, in all eight supported languages, with
our own catalogue answering for the 75 codes iRobot does not document. A code in neither — error
236 has turned up on a real robot — shows as its number rather than an invented name.

Part names follow the same rule: **"Maintenance – High-Efficiency Filter"** keeps this integration's
grouping and takes iRobot's word for the part. So do the maintenance units — `evacs` reads "Dust
Collection Left" in the app, so it does here too, rather than the obvious translation of the field
name.

The point is not tidiness. Somebody comparing the two screens should see the same words; a list
saying "60 evacuations remaining" beside an app saying "Dust Collection Left" reads as two different
measurements of two different things.

## Every option, and what it costs you to leave it off

Settings → Devices & Services → Roomba+ → **Configure**. Nothing here is required; the defaults are
what a robot does out of the box.

### The map

| Option | Default | Notes |
|---|---|---|
| Enable the live cleaning map | on | Robots with position sensing only (900, i, s, j, Braava m) |
| Map canvas size in pixels | 600 | 400–1200 |
| Millimetres per pixel | 10 | 5–30. At 600 px, 10 gives you a 6 m × 6 m room |
| Draw room names | on | |
| Draw clean zones | off | |
| Draw keep-out zones | off | |
| Draw no-mop zones | off | |

The three zone layers are off by default because they clutter a small canvas. **They no longer need
a running mission** — earlier versions could only draw them from live mission data, so ticking the
box and reloading showed nothing.

### Entities you may not want

| Option | Default | Notes |
|---|---|---|
| Show cleaning schedules as a calendar | **on** | |
| Show maintenance as a to-do list | **off** | |
| Create a button for each saved favorite | off | Prime robots. One button per favourite, which is a lot of buttons on a busy account |

The calendar and the to-do list are the same kind of thing and default differently, which is
deliberate: the calendar existed before the option did, and switching it off for people already
using it would have taken something away. The to-do list arrived with its option.

**Both take a place in Home Assistant's sidebar**, which is the user's space rather than an
integration's — that is why the list has to be asked for.

### Presence and demand cleaning

| Option | Notes |
|---|---|
| Enable presence-aware scheduling | Pauses schedules while somebody is home |
| Person or device_tracker entities | Who counts as "home" |
| Presence mode | How they combine |
| Away delay | How long everyone must be gone before schedules resume |
| Delay before second robot start | Waits after the away delay before actually cleaning |
| Enable demand cleaning | Starts a clean when the robot reports unusual dirt |

**Both work on Prime robots as of v4.0.0a31.** They were offered and stored but read by nothing
before that — configured fully, doing nothing, with no error to say so.

### Blocking sensors

| Option | Notes |
|---|---|
| Binary sensors that block a start | Door open, motion detected, anything ON that should stop a mission |
| Blocking behaviour | Queue the start or drop it |
| Blocking timeout | How long a queued start waits before giving up |

### Multi-robot households

| Option | Notes |
|---|---|
| Floor label | Free text, e.g. "Ground Floor". Groups robots in the fleet view |

## Why the floor is getting dirtier

The cleaning performance sensor carries two attributes that only mean something together:

- **`dirt_trend`** — rising, stable or falling, from the median of the five most recent missions
  against the previous ten.
- **`dirt_cause`**, present only when the trend is rising:
  - **`brush_wear`** — rising dirt with *declining* speed. The sensor re-fires on debris the brush
    is not picking up, and the robot slows down carrying it.
  - **`floor_dirty`** — rising dirt with steady speed. The floor is genuinely dirtier.

The first cleans after a gap of more than a week are excluded from both: they run on an abnormally
dirty floor and would otherwise read as brush wear.

Classic robots only — this comes from the cloud mission history.

## What the mission record says went wrong

`error_code` on a mission record now falls back to the first error in that mission's timeline when
the record itself reports none. On one tester's robot the mission-level field was empty on **all 49
records** while the timelines held 111 error events — sixteen missions ended stuck and the records
said nothing was wrong.

## Events & device triggers

Roomba+ fires events on the HA event bus that automations can react to
directly (`platform: event`), and also exposes a curated subset as
**device triggers** in the Automation editor (under "Device" — no YAML
needed). See **[Automations & dashboards →](AUTOMATIONS.md)** for examples
using these.

Roomba+ fires events on the HA event bus that automations can react to directly (`platform: event`), and also exposes a curated subset as **device triggers** in the Automation editor (under "Device" — no YAML needed).

**On Prime robots the device triggers arrived in v4.0.0a31**, and needed no trigger code — they
watch entity state, and Prime was missing the `phase` sensor they all read. Adding one sensor was
the whole fix. Before that, "when the robot starts cleaning" simply did not appear in the editor
for Prime users, with no error to explain it.

### Event bus (v2.8.6+)

| Event | Fires when | Payload |
|---|---|---|
| `roomba_plus_mission_completed` | A mission ends (any result) | `entry_id`, `name`, `rooms_cleaned`, `area_sqft`, `stuck_count`, `result` — plus (v3.2.0) `is_anomalous`, `anomaly_reason`, `recommended_action`, `robot_lifted`, always present (`null`/`false` for ordinary missions), so a notification automation gets the anomaly reason without calling any service |
| `roomba_plus_room_completed` | AUTO-ADVANCE-ROOM confirms a room finished | `entry_id`, `name`, `room_name`, `room_idx` |
| `roomba_plus_health_change` | `sensor.*_integration_health` crosses a band (healthy/degraded/critical) | `entry_id`, `name`, `score`, `previous_score`, `band`, `previous_band` |
| `roomba_plus_map_retrain_started` / `_completed` | Cloud detects a Smart Map change and syncs | `entry_id`, `name`, `pmap_id` |
| `roomba_plus_maintenance_reset` | Filter/brush/battery/pad/wheel/contact/bin reset — button or service | `entry_id`, `name`, `component`, `hours` (`null` for calendar-based resets) |
| `roomba_plus_stuck` (v3.2.0) | MQTT watchdog detects the robot went silent during an active mission | `entry_id`, `name`, `last_room`, `phase`, `stuck_count`, `minutes_stuck`, `last_known_position` (if pose data available) |
| `roomba_plus_all_away` · `roomba_plus_person_detected_during_clean` | Presence-aware scheduling (see above) | — |
| `roomba_plus_start_blocked` · `roomba_plus_start_timeout` | Smart Start blocking-sensor gate (see above) | `blocking_entities` (for `start_blocked`) |

`roomba_plus_mission_completed`, `roomba_plus_maintenance_reset`, and `roomba_plus_stuck` also produce rich, searchable **Logbook** entries automatically — no setup needed.

### Device triggers

Available in the Automation editor's Device trigger picker for every Roomba+ robot:

`cleaning_started` · `cleaning_finished` · `stuck` · `bin_full` · `docked` · `error` · `room_completed` · `maintenance_due` · `health_score_drop` · `map_retrain_started` · `map_retrain_completed` · `firmware_updated`

`health_score_drop` only fires when the health band genuinely *worsens* (e.g. healthy → degraded) — not on every score fluctuation, and not when it improves.

---

*[Roomba+](../README.md) · [Automations](AUTOMATIONS.md) · [API](API.md) · [Comparison](COMPARISON.md) · [Troubleshooting](TROUBLESHOOTING.md)*
