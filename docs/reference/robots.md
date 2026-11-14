[Roomba+](../../README.md) › [Documentation](../README.md) › Robots and generations

# Robots and generations

Which robots Roomba+ supports, how Classic and Prime differ, and the controls of robots that mop.

## Classic and Prime

**Classic and Prime are different generations, not tiers of one.**

- A **Classic** robot talks to Home Assistant over your own network and can work with no internet at all. Adding the iRobot account gives it room names, the floor plan and iRobot's part counters.
- A **Prime** robot (Roomba Max, Combo 400-series and other robots on iRobot's newer cloud protocol) has **no local connection**: everything goes through iRobot's cloud, so the account is required. What works and what is not yet confirmed: [V4/Prime robots](../PRIME.md).

The models and what works on each: [Supported robots](../../README.md#supported-robots).

## How the pages say which robots

Every section of the guides starts with an **Applies to** line, in these words:

| Words | Robots |
|---|---|
| all robots | every supported robot, both generations |
| Classic | robots on the local connection |
| 600-series | bump-and-run robots without a map |
| 900-series | robots that see their position but keep no map between runs |
| Smart Map robots | i, s, j, e with Smart Map, Combo j, Braava m6: they keep maps of rooms |
| with the iRobot account | the account is added under Configure (Classic); Prime always has it |
| Prime | robots on iRobot's newer cloud protocol |
| Braava | the mopping robots of the Classic generation |

## Which generation has what

Feature parity is the goal: a feature is Classic or Prime only where the robot or its
protocol does not give the data. Where that is so, the reason is in
[V4/Prime robots](../PRIME.md#what-prime-shares-with-classic).

| Feature | Classic | Prime |
|---|---|---|
| [Status, controls, errors, debug capture](status-and-controls.md) | ✓ | ✓ |
| Child lock, eco charge · gentle mode | ✓ · ✓ | ✓ · — (not among the Prime settings) |
| [`clean_room`, `vacuum.clean_area`, Smart Start](../guides/clean-rooms.md) | ✓ | ✓ |
| [Skipping the current room](../guides/clean-rooms.md#skipping-the-current-room) | ✓ (button and command) | command only, unconfirmed |
| [Naming rooms, zone management](../guides/clean-rooms.md#naming-rooms-and-areas) | ✓ | rooms and zones come from the maps (map and zone selects) |
| [Room detection](../explanations/how-rooms-are-found.md), [map drift](../explanations/maps.md#map-drift), room type suggestion | ✓ | — (Classic map tiers) |
| [Layout change detection](../explanations/maps.md#layout-changes), coverage map, stuck patterns and hotspots | ✓ | — (built on the local position grid) |
| [Cleaning map, rooms map, floor plan of each map, map colours](../guides/dashboards-and-maps.md) | ✓ | ✓ |
| [Maintenance entries, Configure → Maintenance, care guides, repairs, calendar, to-do list](../guides/maintenance.md) | ✓ | ✓ |
| Remaining life, part counters | ✓ | the part sensors |
| Clean Base status, replacement dates, care reminders | ✓ | ✓ |
| [Robot health](../guides/maintenance.md#robot-health): navigation, dock contacts, health score, reset causes | ✓ | partly: navigation resets, dock counters once reported |
| [Mission log](../explanations/history.md), [progress](../explanations/estimates.md), phase, error explanation, statistics, REST history | ✓ | ✓ |
| Mission anomaly detection | ✓ | the count sensor; the repair issue and events are not checked on Prime |
| Room intelligence, map intelligence, performance sensors | ✓ | — (read Classic cloud history; a Prime variant is planned) |
| [Rhythms, overdue rooms, cadence health](../guides/schedules.md#rooms-that-are-due) | ✓ | ✓ |
| [Schedule calendar, presence scheduling, demand cleaning](../guides/schedules.md), favourites | ✓ | ✓ |
| Pad wash and dry, pad wetness, per-room preferences, [quiet hours, the robot's start-blocking codes](../guides/start-conditions.md) | not recorded on Classic | ✓ |
| [Braava / mop sensors](#braava) | ✓ | tank present |
| [Events and device triggers](events.md) | ✓ | ✓ (map retrain: Classic) |
| [Mission started, areas on room events, current room and estimated end](../guides/scripts-and-assist.md) | ✓ | ✓ (who and where from the last command only) |
| `get_rooms`, `get_missions`, `get_mission`, `plan_clean` | ✓ | ✓ |
| Learning reasons, estimate quality, battery per mission | ✓ | ✓ (battery only for missions seen running) |
| Area last vacuumed / mopped, pause timeout, shipped blueprints | ✓ | ✓ |
| [Cloud stream times](status-and-controls.md#cloud-stream-times) | — (one local connection, `mqtt_stale`) | ✓ |

## Mopping robots

### Braava

**Applies to:** Braava

Sensors: tank level, mop pad type, mop clean mode, mop tank status (`Ready` / `Fill Tank` / `Lid Open` / `Tank Missing`); binary sensors mop ready, tank present, lid closed. Pad wetness: Low / Medium / High, separately for disposable and reusable pads.

### Pad washing and drying

**Applies to:** Prime robots with a wash dock

`switch.{name}_prime_pad_wash_allowed` and `switch.{name}_prime_pad_dry_allowed` turn pad washing and drying on and off, each offered only where the dock can do it.

### Pad wash interval: what the numbers mean

**Applies to:** Prime robots with a wash dock

`select.{name}_prime_pad_wash_area_interval` carries bare numbers (6, 8, 10, 15, 20),
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
your own units: one owner sees "after every 100 ft²" for value 10, while
another sees "5 / 10 / 15 m²" for 6, 10 and 15.

The numbers are kept here rather than translated to High/Medium/Low for two
reasons: **the sets differ per robot series**, so the same word would mean a
different area on different hardware, and a robot can report a value outside
its own set, which has no position to name.

> ⚠️ **The upper values are not confirmed on every dock.** At least one
> dock appears not to accept the top of its own offered range: the value
> is sent, acknowledged, and does not stick. The sets come from the
> vendor's own tables, so this is more likely a per-dock limit than a
> wrong table; if a high value reverts on you, that is what is
> happening, and a report naming your dock would settle it.

### Pad wetness

**Applies to:** Prime robots that report a wetness control

`select.{name}_prime_pad_wetness` writes `padWetness.padPlate`, gated on
`cap.ppWetLvl`. A robot reporting `0` there has no wetness control and gets no
entity: a real difference between two robots of the same model with
different docks.

**The level range is the one value set in this integration with no documented
source.** The key, its addressing and its gate are all confirmed; the range is
in no vendor enum, no settings-key type and no locale string. Four is the
highest anyone has seen, and the picker widens itself to include whatever your
robot reports, so a guessed ceiling cannot hide a real setting.

### The mop tank sensor is unreliable on some Combos

**Applies to:** Prime Combo robots

`binary_sensor.{name}_mop_tank_present` reads the robot's own `tankPresent`
field, and on at least one Roomba Combo **that field describes nothing**.

One owner's Combo 405 reports `tankPresent: true` while the robot has no fill
port, the iRobot app shows no water level for the robot at all, and the value
does not move when either dock tank is removed. On that hardware the pad is
wetted at the dock and there is no onboard tank to be present.

**It is kept rather than removed**, because nothing distinguishes a robot with
a tank from one without: there is no capability flag for it, and `cap.ppWetLvl`
describes pad wetness the dock applies. Withholding it everywhere on one
robot's evidence would take a working reading away from any robot where the
field is real.

**If yours reads `off` when you pull the tank, it works and this note does not
apply to you**, and that observation is worth reporting, because nobody has
made it yet.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
