[← Roomba+](../README.md)

# Troubleshooting

---

## Installing

**HACS shows only `main`, and downloading it hangs**

The log carries a 404:

```
Download failed - Got status code 404 when trying to download
.../releases/download/main/ha_roomba_plus.zip
```

**Enable beta versions in HACS** — Roomba+ → ⋮ → Redownload → "Show beta
versions" — then pick the newest `v4.0.0aNN`. That is the working install
today.

**Why `main` fails:** with betas off HACS finds no eligible release and offers
the default branch instead. It then looks for a release asset on a tag called
`main`, which does not exist — so the download 404s and the wheel spins
forever. Nothing is wrong with your setup.

**Every v4 release is a pre-release**, because v4 is in alpha. With betas off,
HACS has nothing in the v4 line to offer.

**Fixed in v3.5.2**, confirmed installable from HACS's picker. If you are
seeing this on an older checkout: v3.5.1 became unreachable, and v3.5.2 is the
same code re-tagged so HACS can find it. Update normally.

The rest of this section explains why, because it will recur.

**The mechanism, read from HACS's own source** rather than guessed:
`RepositoryBase.get_releases()` makes ONE call to GitHub's release list and
filters what comes back — it does not paginate. GitHub returns 30 releases per
page by default, so HACS only ever sees the 30 most recent.

There are now **37 v4 pre-releases** on top of v3.5.1. All thirty HACS sees are
pre-releases, every one is skipped when betas are off, and it ends up with an
empty list — hence the fallback to the default branch. With betas on you get a
v4 alpha instead, not v3.

This is also why it used to work: the stable release stayed reachable until the
**thirtieth** alpha pushed it out of the window.

**Install v3.5.1 manually:** download the `ha_roomba_plus.zip` asset from the
[v3.5.1 release](https://github.com/johnnyh1975/ha_roomba_plus/releases), unpack
it into `config/custom_components/roomba_plus/`, and restart. HACS will then
show it as installed but will not offer updates for it.

That is a consequence of running a long alpha in the same repository as the
stable line, not something you did wrong.

---

## Setup & connection

**"Failed to connect" during setup**

Press the physical **Clean** button on the robot to start a manual cleaning job, then immediately retry credential retrieval in HA. Some models only respond while actively running.

If automatic pairing fails entirely: → [Retrieve iRobot credentials manually](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

---

**The iRobot app loses connection when Roomba+ is running**

Expected — the robot only allows one local MQTT connection. Either disable continuous mode in Settings → Roomba+ → Configure → Connection settings, or accept that the iRobot app will use the cloud path while Roomba+ is connected locally.

---

**Cloud authentication fails**

Check your iRobot app email and password. If you see an "mqtt slot" error, close the iRobot app on all devices and wait a few minutes before retrying.

---

**Cloud mission history not available for my Roomba 980**

Go to Settings → Roomba+ → Configure → iRobot cloud credentials, re-save your credentials, then restart HA.

---

## Zones & rooms

**I want to clean one zone on demand, and the app no longer lets me**

The iRobot app dropped zone favourites — rooms only. Use
`roomba_plus.clean_zone` instead (4.0.0a42 and later):

```yaml
action: roomba_plus.clean_zone
target:
  entity_id: vacuum.your_robot
data:
  zone_name: ["Clean Kitchen"]      # or zone_id: ["100"]
```

Names come from the map bundle's `cleanZones` layer — the same names the
map card shows. If a zone has no name there, use its numeric id.

An unknown name raises an error listing the ones that exist, rather than
skipping it silently: a partial clean looks like a successful one.

---

**Before you send a diagnostics download**

Credentials and your BLID are removed automatically. Room names, zone names,
schedules, map geometry and mission history are **not** — the download is
useless without them. [What is in it, in full →](DATA_PRIVACY.md)

A partial download is more useful than none: send the block that matters if
the rest gives you pause.

---

**Smart Map zones not appearing (i/s/j-series)**

Check that `"cap": {"pose": ...}` in the diagnostics download shows a value ≥ 1. If cloud credentials are configured, zone names come directly from the cloud and the naming repair flow is suppressed.

---

**Zone management — changes not reflected in dropdown immediately**

Alias and hidden changes are written immediately, but the zone select dropdown may take one MQTT message cycle to refresh. Typically resolves within seconds when the robot is active.

---

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

---

**`clean_room` or `vacuum.clean_area` raises `no_valid_segments` immediately after setup**

The active pmap ID may not yet be available — typically happens on fresh install or immediately after adding cloud credentials before the first cloud fetch completes. Wait a few minutes and retry. If it persists, check that `region_count_active` in diagnostics is > 0.

---

**"Map vacuum segments to areas" repair fires — what do I do?**

This is a one-time HA setup step for `vacuum.clean_area` (HA 2026.3+). Open the vacuum entity → ⚙ Entity settings → "Map vacuum segments to areas", match each robot room to a Home Assistant area, and save. See [Room cleaning setup](FEATURES.md#room-cleaning-setup--ha-areas-vacuumclean_area-ha-20263) in the Feature reference for full instructions.

If you don't use `vacuum.clean_area`, dismiss the repair — `roomba_plus.clean_room` works without it.

---

**"Map vacuum segments to areas" dialog shows no rooms on the left**

The left column is populated from the iRobot cloud, so an empty list means the integration has no room data to offer yet. Check, in order:

- **Cloud credentials configured?** Room segments require the cloud coordinator (Settings → Configure → iRobot cloud credentials). Local-only setups have no room names to map.
- **SMART robot?** Only i/s/j-series robots expose cloud rooms. 900-series (EPHEMERAL) robots have no cloud pmap, so they list no segments here and don't support room- or area-targeted cleaning at all (`clean_room` and `vacuum.clean_area` are SMART-only) — they clean the whole floor.
- **Map finalized in the iRobot app?** If rooms aren't named/saved in the iRobot app, the cloud returns none. Open the app, confirm the room layout, then re-save the cloud credentials step in Configure to force a coordinator refresh.
- **First fetch completed?** On a fresh install, wait a few minutes for the first cloud poll, then reopen the dialog. Check `region_count_active` in the diagnostics download is > 0.

---

**Mission progress gets stuck reporting a completed room as still in progress**

On lewis-firmware robots (i7+/s9+), the robot occasionally reports a brief non-cleaning phase between rooms that can confuse the progress sensor. As of v2.8.0, Roomba+ detects these transitions automatically using your robot's real per-room cloud time estimates, so this should self-correct within the next room change. If it doesn't, call `roomba_plus.advance_room` to manually move to the next room — it's a no-op if the robot is actively cleaning or already at the last planned room, so it's safe to call speculatively.

---

## Mission sensors on Prime robots

**`clean_streak` or `area_cleaned_today` only updates after a reload**

Fixed in 4.0.0a42. Mission records for Prime robots come from the cloud
history sync, and that sync used to run at setup and then every six
hours — so a mission finishing at 07:00 was invisible to these sensors
until the next tick or the next reload.

It now syncs the moment the robot reaches `charge` on the dock. If you
still see a delay after a mission ends, that is worth reporting.

---

**`clean_streak` drops to 0 after midnight**

Also fixed in 4.0.0a42. The count was tied to "today", so an unbroken run
of any length read 0 every night until the day's first mission.

A streak may now end today **or** yesterday: not having cleaned yet today
does not break it, but a whole missed day does.

---

**`clean_streak` shows a decimal like 9.89725**

Fixed in 4.0.0a41 — the sensor was declared as a measurement, so history
graphs interpolated between whole numbers. If you still see decimals,
the old statistics remain in the database; they will age out, or you can
clear them under Developer tools > Statistics.

---

## Sensors showing Unknown

**`filter_last_replaced` / `brush_last_replaced` shows Unknown**

These sensors are Unknown until the first reset is performed. Press the reset button on the device page or call the reset action to populate them.

---

**Wear Intelligence sensors show Unknown**

Wear sensors need at least 3 days of mission data since the last reset to calculate a meaningful rate. They populate automatically.

---

**`optimal_clean_window` shows Unknown**

Requires at least 5 completed missions since integration setup. Updates automatically within minutes of the next mission end once 5 missions have recorded.

---

**Mission log sensors show Unknown after upgrading**

The mission log is populated going forward only. Streak, completion rate, and area sensors will be Unknown until the first mission completes after upgrading — this is expected.

---

**`last_error_code` shows a stale error after the robot has recovered**

The error state clears automatically when the next mission completes successfully. If it persists, restart HA to force re-reading the mission log from storage.

---

**Total energy consumed shows an unexpected value after upgrading to v2.5 on a Roomba 980**

Expected — v2.5 corrects the energy calculation for 900-series robots. The 980/985 firmware reports a raw BMS value approximately 3.73× the actual mAh; previous versions used this raw value directly. After upgrading, the sensor shows the correct lower value and continues accumulating from that point.

---

**Recent cleaned area / cleaning time show lower values than expected**

These sensors aggregate data from the iRobot API window (~30 recent missions). The iRobot API does not expose a lifetime accumulator for area or time — the `source: recent_mission_window` attribute documents this. The **total missions** sensor is different: it reads the lifetime counter embedded in every cloud record.

---

**Dock contact / Wi-Fi channel / optical dirt detection sensors aren't showing up at all**

These (and most other diagnostic-category sensors) are disabled by default to keep the entity list manageable — they don't show as `Unknown`, they simply aren't enabled. Go to the device page → entity list → filter by "Diagnostic" → enable the ones you want. Availability also depends on your robot's series: navigation landmark quality is 9-series only, optical/piezo dirt detection and dock contact counters are i/s-series only.

---

## Demand cleaning & scheduling

**`demand_clean_blocked` stays ON even though the robot is idle**

Check all four gates: (1) robot cycle state — `vacuum.{name}` must be `docked` or `idle`; (2) blocking sensors — any configured blocking sensor is ON; (3) presence — all tracked persons must be away if presence mode is `away_only`; (4) minimum gap — 6 hours must have elapsed since the last demand trigger. The `blocking_reason` attribute on the sensor names the active gate.

---

**Demand cleaning never triggers despite being enabled**

Check `binary_sensor.{name}_demand_clean_blocked` — it shows whether the robot is busy, a blocking sensor is active, or the 6-hour minimum gap has not elapsed. Also ensure cloud credentials are configured and at least 5 cloud mission records exist (check diagnostics for `region_count_active`).

---

**Demand cleaning triggers too often / not often enough**

Adjust the trigger multiplier in Configure → Demand cleaning. `1.5` (default) fires when dirt density is 50 % above the baseline for today's weekday. Lower the multiplier for more frequent triggers; raise it to require dirtier conditions. After v2.5 the baseline is weekday-specific — Monday's threshold is set by Monday's history — so the multiplier applies relative to each day's normal level.

---

**Self-calibrating filter/brush thresholds: when do they activate?**

After two or more resets of a given component, Roomba+ computes the median interval between resets and uses that as the effective threshold. Until two resets have been performed, the configured threshold is used. The learned values are visible in diagnostics under `learned_maintenance`.

---

**Presence-aware scheduling step not visible in options menu**

The presence scheduling step only appears for robots that report `schedHold` in their MQTT state (i/s/j/Braava m6). It will not appear for 900-series or 600-series robots.

**Prime robots take a different route to the same result.** A Prime robot carries `schedHold` in its
shadow, accepts a write to it, and runs anyway — iRobot's own app has no consumer for the field at
all. Presence scheduling therefore disables each schedule instead, which is what the app does. One
visible difference: a paused Classic schedule still looks scheduled in the iRobot app, while a
paused Prime one looks switched off. It is — but it is not what Classic does.

---

**`smart_start` queues forever / never starts**

Check that the blocking sensors are reporting correctly. Unavailable or unknown sensors are treated as non-blocking. If the queue expires, `roomba_plus_start_timeout` is fired — automate on this event to alert or retry.

---

## Mission anomaly detection

**"Unusual cleaning patterns" Repair Issue fires for normal short cleans**

The anomaly detection (v2.5+) uses your robot's personal performance history as the baseline. If the flag fires for a normal targeted single-room clean, the single-room area is far smaller than your typical full-home baseline — which is technically correct. The issue self-resolves: if the next mission is normal, the counter resets and the issue clears. Two consecutive anomalous missions are required to fire the issue.

---

## No favourite buttons

Favourites from the iRobot app appear as one button each on Prime robots. If they are missing,
**download diagnostics** — the `favourites` block reports how many reached the integration and
whether the buttons are switched on, which tells apart the two reasons they could be absent.

A count of zero on an account that has favourites is worth reporting. Versions before v4.0.0a32
returned nothing when the server wrapped the list in an object rather than sending a bare array.

## A Prime robot that says it is cleaning and is not

**Symptoms.** The vacuum shows `cleaning` for hours or days. Scheduled missions stop running and
nothing reports an error. Commands from Home Assistant — start, stop, dock, find — are accepted and
have no effect. The iRobot app shows the same phantom mission and its End Job button does not clear
it.

**What is happening.** A mission that ends in an error can leave the robot's cloud document stuck at
`{phase: "run"}` with no terminal state ever written. The robot is fine and still talking — it keeps
reporting its battery — but every consumer reading that document, including iRobot's own app, sees a
mission that never ended.

Observed on a Roomba Combo (Y351020) for **61 hours** after an error 48 mission, during which two
daily schedules were skipped silently and every remote command was swallowed.

**How to recognise it.** Roomba+ reports `phase` as **`stale`** when the robot's battery is rising
while the document claims it is running, and has been running for more than ten minutes — charging and cleaning are mutually exclusive, and the
robot supplies both numbers. `readiness` will read `NONE` throughout: the sensor whose job is "why
won't it start" has no answer, because the robot claims nothing is blocking.

**Why this matters beyond the display.** In this state the robot ignores commands from everywhere —
Home Assistant, the app, an automation — while accepting every one of them. A `vacuum.start` that
does nothing is not a broken integration; it is a robot whose cloud document has stopped tracking
it. If `phase` reads `stale`, that is the explanation, and the fix below is the whole of it.

**What does not fix it:**

- `vacuum.stop` from Home Assistant — delivered, no effect
- End Job in the iRobot app — no write is even attempted
- Running a mission with the physical button — a locally started mission appears nowhere in this
  document, before, during or after
- Docking at the end of that mission

**What does fix it: power cycle the robot.** Hold the power button until the light ring goes dark,
then wake it. On reconnect it re-reports its true state, and the cloud finalises the stuck record —
in the observed case closing a 61-hour phantom as `ok` at a timestamp matching the reboot to the
minute.

Nothing softer reaches it, and no integration can do this for you.

**Why ten minutes.** A robot that recharges mid-mission and resumes enters `run` while its battery
is still climbing from the charge — genuinely running and genuinely rising, the one case where this
test would lie. The freeze rises for hours, so the delay costs nothing; without it, an automation
watching for `stale` would be woken by a healthy robot finishing a recharge.

**The error text is on the error sensor.** `error_title` and `error_description` carry iRobot's own
wording as attributes, so an automation can say what went wrong rather than quoting a number. Error
48 reads "An obstacle blocked the entrance to a room" — on one tester's robot that single code
accounted for 93 of 111 timeline errors and every incomplete mission in the archive.

## The live map does not match the room

Two things behave differently since v4.0.0a43, and both are deliberate.

**Gaps in the trail.** The path now breaks where the robot's reported
position jumped further than it could have driven — a relocalisation, or
someone picking it up. Previously the line was drawn straight through,
which asserted travel that did not happen. A gap is the honest picture
of "the robot was here, then it was there, and nothing connects them".

Frequent gaps during ordinary cleaning are worth reporting: the
threshold measures itself from the robot's own message rate, so it
should only trigger on genuine discontinuities.

**Coverage after a pickup may be missing.** If the robot is moved
mid-mission, the stretch before the move sits in an unknown position.
Roomba+ tries to place it by matching its coverage pattern against area
it already knows, and leaves it out of the stored map when the match is
not clear. It still appears in the live view for that mission. Losing a
stretch costs coverage the next mission re-drives; storing it at the
wrong offset would corrupt the map permanently.

## Rooms are wrong, or fewer than expected

**Rooms need three completed missions.** Nothing appears before that,
and this is on purpose — see [Room detection](FEATURES.md#room-detection--900-series-v2100)
for why an early map produces rooms that later disappear.

**The shape follows coverage, not walls.** Room outlines are built from
where the robot drove, so furniture leaves holes and edges fall short of
the wall by roughly the robot's radius. Boundaries land at narrow points
in the coverage, which usually but not always means a doorway. More
missions improve the count; they do not change the shape.

If a room is split that should be one, check whether the doorway between
them has actually been driven through — an uncrossed threshold reads as
a wall. Merging is available in the Options Flow.

## Cloud & history

**Mission history export**

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

**Mission history import**

```bash
curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d @roomba_backup.json \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history/import"
```

Import deduplicates by `id` — safe to run multiple times. Returns `{"imported": N, "skipped": N, "errors": []}`.

The `entry_id` is found in Settings → Devices → Roomba+ → ⋮ → System information.

---

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

---

**"Robot MQTT connection lost during mission" fires right after starting a mission**

Fixed in v2.9.0. A genuine, benign Wi-Fi gap of a few minutes right after undocking (the robot reassociating with your router while it physically moves away) was previously misreported as a sustained connectivity problem — this affected any robot, but was more common on older robots with weaker Wi-Fi hardware or an aftermarket battery. The watchdog now waits at least 7 minutes after a mission starts before it can fire at all, regardless of silence duration; a genuine outage later in the mission is still caught normally. If you still see this fire within the first 7 minutes of a mission on v2.9.0 or later, that's unexpected — please open an issue with the last known phase and silence duration shown in the message.

---

**Replacing or selling your robot**

Roomba+ stores months of learned data — mission history, coverage baselines, maintenance timers, and health trends — inside HA. Before removing or selling a robot, back up that data so you can restore it if you reinstall later, or hand it off to the new owner.

**Step 1 — Export your history (optional but recommended)**

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

The `entry_id` is in Settings → Devices → your Roomba → ⋮ → System information.

**Step 2 — Remove the integration**

Go to Settings → Devices & Services → Roomba+ → Delete. This removes the config entry, all associated entities, and every file this integration stored on disk for this robot *(v3.4.0+)* — mission history, coverage baselines, maintenance timers, robot profile, and the rest. Nothing lingers after deletion; this is why Step 1's export matters if you want to keep the history.

**Step 3 — Factory reset (if selling)**

A factory reset on the robot is done through the **iRobot app** (not this integration): open the app → your robot → Settings → Factory Reset. This clears the robot's stored map and account link. Roomba+ has no factory-reset command — the robot's firmware handles this directly.

**Setting up a replacement robot**

Add a new config entry for the new robot (Settings → Add Integration → Roomba+). The new entry starts fresh. If you want to restore history from a previous robot, use the import endpoint after setup:

```bash
curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d @roomba_backup.json \
     "https://<ha>/api/roomba_plus/<new_entry_id>/mission_history/import"
```

Import deduplicates by `id` — safe to run multiple times.

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [Automations](AUTOMATIONS.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
