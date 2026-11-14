[Roomba+](../../README.md) › [Documentation](../README.md) › Maintenance

# Maintenance

Know when a part is due, mark it done, get reminded, and follow a care guide. The care guides themselves, without Home Assistant: [docs/care](../care/README.md).

**Applies to:** all robots, both generations. Where the two differ, the section says so.

**On this page:** [The maintenance entries](#the-maintenance-entries) · [Configure → Maintenance](#configure--maintenance) · [The to-do list](#the-to-do-list) · [Care guides](#care-guides) · [Reminders, repairs and the calendar](#reminders-repairs-and-the-calendar) · [Where an interval comes from](#where-an-interval-comes-from) · [Parts counted by iRobot](#parts-counted-by-irobot) · [The part sensors](#the-part-sensors) · [Robot health](#robot-health)

## The maintenance entries

**Applies to:** all robots

Every part and care task is one **maintenance entry**, worked out once and shown the same everywhere. `sensor.{name}_maintenance` gives the worst level of the entries that are not snoozed (`ok`, `soon`, `due`, `overdue`) and lists them in `items`:

| Field | Meaning |
|---|---|
| `id` | `replace.filter`, `replace.main_brush`, `clean.wheel`, `replace.part_202` ... |
| `action`, `part` | `replace` or `clean`; the part or task |
| `level` | replacing: soon from 80 % of the interval, due at 100 %, overdue a week later; cleaning: due on its date, overdue after half an interval more |
| `used`, `remaining`, `unit` | share of the interval used; what is left, in hours, days, missions, evacuations, pad washes ... |
| `due` | when it is due (or became due); from the wear rate for parts |
| `interval`, `interval_unit`, `interval_source` | `cloud` (the robot's counter), `learned`, `configured`, `profile`, `irobot_budget`, `calendar` |
| `last_done`, `wear_rate` | last replacement or cleaning; use per day |
| `guide_url`, `buy_url` | iRobot's guide and shop link, where its parts catalogue has them |
| `guide_id` | our care guide for the entry (`clean_filter`, `replace_bag` ...), see [Care guides](#care-guides) |
| `done` | what marking it done writes: `local`, `cloud`, `both` |
| `snoozed_until` | set by `maintenance_snooze` |

`event.{name}_maintenance` fires `due`, `overdue`, `done` and `snoozed` with the entry as data, once per change: a restart does not repeat it, and a reset in the iRobot app counts as done. Its `guide` is the signed link to the entry's care guide and `guide_title` the guide's title in Home Assistant's language ("Filter reinigen"), for a notification; the [maintenance blueprint](../AUTOMATIONS.md#maintenance-450) uses both.

`binary_sensor.{name}_maintenance_due` is on when a part to replace is due or overdue, by the same rule. Its attributes `due` and `overdue_by_hours` say which; the default dashboard tile shows only on or off, so open the entity's dialog to see them. It is also the *Maintenance due* device trigger.

**Marking done:** tick the item off in the to-do list, use Configure → Maintenance → *Report a part replaced or a task done*, press the part's reset button, or call `roomba_plus.maintenance_done` with `item` (and `action: clean` to report a part as only cleaned: its counter stays). `roomba_plus.maintenance_snooze` with `item` and `days` silences an entry for a while. On a Prime robot the reset is sent to iRobot the way the app sends it; if iRobot applies it to no part, the action fails with a message. Every reset writes a Logbook entry and fires `roomba_plus_maintenance_reset` ([Events](../reference/events.md)).

On Prime, a part gets its name from iRobot's part name, as in the app: the first of mop washing system, dock washing system, main brush, side brush, detergent, filter, pad, bag, roller, sensor found in it. Care tasks on Prime use the 2025 lines' intervals: side brush, cliff sensors and charging contacts every two weeks.

## Configure → Maintenance

**Applies to:** all robots; the hour options and *Forget learned intervals* on Classic

One page for maintenance, on both generations:

- **The overview**: every entry with its level, what is left and where the interval comes from.
- **Settings**:

| Setting | Values | Default |
|---|---|---|
| Show maintenance as a to-do list | on / off | on for a robot added from 4.5.0; a robot set up before keeps its setting |
| Cleaning reminders, Pets in the household | on / off | off |
| Report a part as "soon" from | 80 % / 60 % / never | 80 % (iRobot's Prime app warns there) |
| Guide when an error points to a care task | maintenance list / repairs / off | maintenance list; the list choice needs the list on |
| Repair issue for a part a week overdue | on / off | off |
| Maintenance calendar | on / off | off |
| Hide the maintenance diagnostic entities | on / off | off |
| Filter, main brushes, side brush, Clean Base bag interval | hours, empty = automatic | empty; Classic only: a Prime robot's counters are its intervals |

- **Report a part replaced or a task done**: pick the entry, optionally "only cleaned"; the same as ticking it off or `maintenance_done`.
- **Care guides**: the robot's guides, the most urgent first; the chosen one with its drawing, and "mark as done now" where it has an entry.
- **Forget learned intervals** (Classic): the interval learned from your own replacements, for one part or all, after a reset pressed by mistake or a part swapped early. The current part's count stays; learning starts again with the next two replacements. Also `roomba_plus.maintenance_forget_learned`.

**A new installation is lean.** "Last replaced", "wear rate" and "days until due" per part, and the battery, wheel, contact and bin buttons, are created disabled: their figures are attributes of the part sensors and entries of the status sensor. A 980 starts with nine maintenance entities: the status, three remaining-hours sensors, the due sensor, the list, the event, and the filter and brush buttons. An installation from before 4.5.0 keeps what it has; *Hide the maintenance diagnostic entities* takes it to the same set and back. Dashboards and automations using those entities stop updating while they are hidden.

## The to-do list

**Applies to:** all robots

`todo.{name}_maintenance` shows the maintenance entries as Home Assistant to-do items, in Home Assistant's language. Configure → Maintenance → Settings → *Show maintenance as a to-do list*.

- **One item per entry**: every part the robot has (filter, main brushes, side brush, Clean Base bag or dust bag, the mop pad on a Braava; on Prime every part the robot reports), and with care reminders on every care task.
- **Open from "due"**, not from "soon": while nothing is due the list has nothing open. An item not due is listed as done, with its due date; a snoozed one stays done until its snooze ends.
- **The description** says what is left (or how often a care task comes round), the error that brought the item, and links: our care guide, iRobot's guide and the part where the parts catalogue has them, and iRobot's article for the error. The list shows it as formatted text with clickable links, in the browser and the companion app.
- **Ticking an item off** is `roomba_plus.maintenance_done`: counter, Logbook entry, `roomba_plus_maintenance_reset`, and iRobot's counter where the account has one. On Prime the reset goes to iRobot; if iRobot applies it to no part, the list shows the error.
- **A robot error that points to a care task** brings a "check" item while the robot reports it ("Check the main brushes" for a brush error, "Check the bin" for a bin error), and it goes when the error does. Where the task has an item of its own, that item carries the error instead: never two items for one task. With care reminders on, a dirt error (filter, main brushes, side brush, cliff sensors) makes the cleaning due until it is ticked off; a wheel or contact error only points to the task, and so do a Classic robot's 6 and 25 ("stuck near a cliff", usually a real edge). Which error points to which task follows iRobot's help page for the code; on Prime errors 2, 4, 5, 6, 14, 26, 104, 105, 119, 121, 201, 517, 1000 and 1001.
- Item ids: `filter_maintenance`, `care_wheel`, `part_147`, …; newer kinds use the entry id (`check.bin`).

Smart Map robots also get a **Reconfigure rooms** item while a room on the map has no name anywhere. It cannot be ticked off: it goes by itself once every room has a name (Configure → *Rooms & zones*).

## Care guides

**Applies to:** all robots

A guide for every care task and part (filter, main brushes, side brush, wheels, cliff sensors, charging contacts, bin, camera window, Clean Base bag, battery, mop pad, roller mop, detergent and the wash dock), written and drawn for Roomba+, in all eight languages. Each says what the part does, how to clean or replace it in three or four steps and what not to do, with a drawing of the robot's underside, bin, Home Base, Clean Base or wash dock and the part highlighted. iRobot's own guide for the model is linked below where the parts catalogue has one.

**The guide follows the robot.** Where the series differ, the text does too: the filter of a 600, of an 800/900 (yellow tab under a filter door), of the e, i and j series (out to the left of the bin) and of the s series (by its loop behind a door); how each series releases its brushes; the s series' corner brush and rear caster; the charging contacts on a Home Base, a Clean Base or a wash dock, drawn beside the robot. A Prime robot reads the general text on the e/i/j drawings: iRobot describes its parts per model only for the earlier lines. Mop pads, washable or disposable, share one text.

**Cleaning materials as iRobot gives them:** a slightly damp melamine sponge or cloth for the charging contacts and the cliff sensors; optical windows, the Home Base's docking sensor and a wash dock's markers dry; never a spray.

Where they appear:

- the maintenance list and calendar ("How to do it", beside iRobot's guide), whose descriptions start with what the part does;
- `event.{name}_maintenance` (`guide`, `guide_title`), and the vacuum's `care_task` / `care_guide` while the robot reports something that points to a care task: an error, a refusal to start (it sees a drop, a brush is stuck, the bin is missing, the camera does not answer) or, without a Clean Base, a full bin. For a notification:

```yaml
action: notify.mobile_app_phone
data:
  message: "{{ trigger.to_state.attributes.care_task }} needs attention"
  data:
    url: "{{ trigger.to_state.attributes.care_guide }}"          # iOS
    clickAction: "{{ trigger.to_state.attributes.care_guide }}"  # Android
```

- Configure → Maintenance → *Care guides*, with *When*: the interval and where it comes from, what is left and when it was last done;
- the repair issues below, with the whole guide and its drawings;
- **on GitHub, without Home Assistant:** [docs/care](../care/README.md), every guide in every language, a section for each group of robots it differs for. Made from the same texts and drawings by `scripts/build_care_docs.py`; corrections go into `custom_components/roomba_plus/care/`, not into those pages.

**The link opens without signing in**, in the browser and in the companion app: it is a path signed with this installation's own secret, valid for 31 to 37 days, and the page holds nothing private. An expired link says where to get a new one, and links to the guides on GitHub. The link is relative (`/api/roomba_plus/care/...`); outside Home Assistant's own apps put your Home Assistant address in front.

## Reminders, repairs and the calendar

**Applies to:** all robots; care reminders for a Braava only cliff sensors and contacts

### Care reminders

**Off by default.** Configure → Maintenance → Settings → *Cleaning reminders*. Off, there is no reminder sensor, no "cleaned" buttons and no cleaning items in the to-do list; replacement reminders do not depend on it.

`sensor.{name}_next_care` says when the next cleaning is due, by iRobot's care calendar: filter and main brushes once a week (twice a week with *Pets in the household* on), side brush once a month, wheels every two weeks, cliff sensors and charging contacts once a month. A Braava has the last two only. The state is the earliest date; attributes: `task` (which one), `overdue` (the tasks past due), `pets`, and `schedule` (each task with `last_done`, `due`, `interval_days`, `overdue`).

Each task counts from its last "cleaned" button press or from the moment the option was switched on, whichever is later, so switching it on does not open with every task overdue. Switched off and on again, they start afresh. Replacing a filter, the main brushes or the side brush counts as cleaning it; a cleaning moves no replacement counter.

| Button | Robots |
|---|---|
| Filter replaced, Battery replaced | all |
| Brush replaced | vacuums |
| Mop pad replaced | Braava |
| Wheels cleaned, Charging contacts cleaned, Bin cleaned | all (wheels, contacts and bin go by the calendar, not by hours) |
| Filter cleaned, Main brushes cleaned, Side brush cleaned | vacuums, with care reminders on |
| Cliff sensors cleaned | all, with care reminders on |

### Repair issues (opt-in)

Both under Configure → Maintenance → Settings, off by default; the list says the same:

- **Repair issue for a part a week overdue**: one issue per part, with its guide and its interval (and where it comes from). *Replaced* resets the counter (on Prime at iRobot); *Only cleaned* and *Not yet* ask again in a week; *Never for this part* stops it until the option is switched off and on.
- **Guide when an error points to a care task** set to *Repairs*: one issue while the robot reports the error, refusal or full bin, saying which, instead of the list item. *Cleaned* records the cleaning where the task has an entry; *Close* keeps it away until the next error. It goes by itself when the error does.

### Maintenance calendar (opt-in)

`calendar.{name}_maintenance` is read-only and separate from the schedule calendar: every due date as an all-day event, titled like the list item, with the guide link. A part's date is an estimate from its wear rate and moves with every run, and its description says so; a care task's date stands. Off by default, because a calendar entity puts the calendar in the sidebar.

## Where an interval comes from

**Applies to:** Classic robots; on Prime the robot's own counters are the intervals

Best first: iRobot's own counter for this robot (`cloud`), the interval learned from your own replacements (`learned`), an hour option (`configured`), the robot's series (`profile`), and a last fallback (`irobot_budget`). The part sensor's `interval_source` says which. Series values, in hours of use:

| Series | Filter | Main brushes | Side brush | Clean Base bag |
|---|---|---|---|---|
| 600 | 60 | 120 | 120 | 29 |
| e | 60 | 150 | 150 | 29 |
| 900 (and other `R…` SKUs) | 60 | 150 | 150 | 29 |
| i | 52 | 312 | 157 | 29 |
| j | 60 | 312 | 150 | 29 |
| s | 60 | 200 | 200 | 29 |
| Braava, Combo, unknown models | 52 | 312 | 157 | 29 |

The series values are research figures; the i-series is corrected to an i3+'s cloud counters and the j-series' main brushes to a j9+'s. The last row is the fallback: the i3+'s budgets, iRobot's own figures for one robot. Where iRobot publishes a month figure for the part, `replace_every_months` carries it as `[min, max]`, for information; the count itself is in hours of use.

**Learned from your replacements.** After two or more replacements of a part, Roomba+ learns your own interval from the hours between them (diagnostics: `learned_maintenance`). A reset recorded at 0 hours does not count, nor does a gap shorter than a quarter of the part's series interval (13 h for a 52 h filter): two resets close together, or a part swapped that early, say nothing about its life.

**The hour meter after a restart.** The parts count with the robot's hour meter. A 600 does not send it again after Home Assistant reconnects, so Roomba+ keeps the last reading it saw and counts with that until the robot sends a new one. A part reset in that window is recorded at that reading. Diagnostics: `maintenance.last_known_hr`.

**A robot that has been used before.** The first time Roomba+ sees a robot with many hours on it, the countdown assumes the parts are fresh rather than reading the robot's whole life as overdue. Press the reset buttons when you actually replace something. With the iRobot account added, the robot's real service history replaces that assumption (next section).

## Parts counted by iRobot

**Applies to:** robots with the iRobot account; on Prime every part sensor is one.

iRobot's cloud counts each consumable itself, in runtime minutes, and that is what the app's maintenance tiles show. Where Roomba+ has a sensor for the part, it reads that counter instead of estimating:

| Sensor | Source with the account |
|---|---|
| Filter remaining hours | iRobot's filter counter |
| Brush remaining hours | iRobot's **main** brush counter |

Robots or accounts that report no parts keep the local estimate. Parts with no local wear signal get their own sensors, created only when the account reports that part (a Braava gets no edge-brush sensor, a robot without a Clean Base no bag sensor):

| Sensor | Notes |
|---|---|
| Edge sweeping brush | The side brush. Wears on its own schedule, so it is not averaged into the main brush sensor |
| Dust Bag | The Clean Base bag |

- **A guide link per part.** Where iRobot's parts catalogue has a replacement guide for a part, its sensor carries it as `guide_url`: the manufacturer's page, in your Home Assistant language, for your model. The catalogue is read once per setup without logging in; a part without a guide has no attribute.
- **Your history from the app.** A part's counter includes replacements you confirmed in iRobot's app, so the runtime at each replacement is known, and Roomba+ takes it as its starting point at the first cloud refresh. That is also what gives the *days until due* sensors something real to work from. It does not count as a replacement of your own and does not change the learned interval.
- **Resets go both ways.** A reset button also records the replacement with iRobot, so the app and Home Assistant agree. Best effort: the local reset is saved first, and a cloud failure is logged without failing the action. The edge brush and the bag write to the cloud counter directly.
- **Part numbers.** The account names parts by number, and the numbers differ between robot families. Roomba+ names only the ones it has evidence for (`IROBOT_PART_ROLES` in `const.py`); an unknown part keeps its number rather than a made-up name. The Prime part sensors and their ids are in [Entities](../reference/entities.md#sensors).

## The part sensors

**Applies to:** Classic robots; Prime robots have the part sensors under [Parts counted by iRobot](#parts-counted-by-irobot)

| Sensor | Notes |
|---|---|
| Filter remaining hours | `threshold_hours` attribute |
| Brush remaining hours | Main and side brushes together: the local connection reports one wear signal for both. If the app lists them separately, this one sensor covers both |
| Cleaning pad remaining hours | Braava only |
| Battery capacity retention (%) | Capacity against the design capacity, corrected for the robot's battery profile |
| Estimated battery end of life (days) | Days until the battery needs replacing, measured against this robot's own measurement noise, so a new battery does not get a decades-long projection |
| Filter / brush wear rate (h/day) | Recalculated after each reset; `Unknown` for the first 3 days |
| Filter / brush / pad days until due | At the current wear rate |

All remaining-hours sensors read the maintenance entries' calculation, the same figure the due check uses. Attributes: `level`, `used`, `due`, `last_replaced`, `wear_rate`, `buy_url`.

## Robot health

**Applies to:** Classic robots; on Prime the navigation resets and the dock counters once the robot reports them

Signals that something other than a consumable needs attention. Each raises a repair issue and clears it on its own once the signal is back to normal.

- **Battery or dock contacts** (`battery_contact_suspect`): the battery level jumps by more than about 25 points within 10 minutes, or the highest level reached falls over three charge cycles in a row. Both usually mean a loose or dirty contact rather than a failing battery: clean the contacts on the robot and the dock first.
- **Dock contact health** (i and s series, some 900s): the dock-contact counters `nChatters`, `nKnockoffs` and `nAborts` pass their thresholds, an early sign of contact wear.
- **Navigation health** (i and s series; `sensor.{name}_relocalisation_rate`, off by default): how often the robot has to find itself again during a mission, a direct signal of Smart Map quality. Learned against the robot's own rate over its first 15 missions; the issue comes at three times that.
- **Health score trend** (`sensor.{name}_health_score_trend`): `improving`, `stable` or `declining`, against the robot's own baseline. It needs 44 days of history (`days_until_ready` counts down); the issue comes after 14 days of decline.
- **Reset causes** (`sensor.{name}_reset_diagnostics`, diagnostic): the robot's own count of navigation, mobility, safety and out-of-memory resets.
- **More counters, off by default:** battery capacity (mAh), navigation panics, cliff events front and rear, landmark quality (900), optical and piezo dirt detections and navigation orientations (i and s), dock contact chatters, knockoffs and charge aborts, Wi-Fi channel and missions per charge.

`roomba_plus.reset_robot_profile` clears what Roomba+ has learned about the robot (dirt levels and the room dirt index, coverage and relocalisation baselines, mission duration and area statistics, battery noise, dock heading) so it learns afresh after a move or a big change of furniture. Mission history and the maintenance counters are kept.

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
