[Roomba+](../README.md) › [Documentation](README.md) › Troubleshooting

# Troubleshooting

Find the area of your problem below, then the symptom. Each entry says which robots it
applies to, the cause in a sentence and the fix, then the details. If nothing here matches, open an [issue](https://github.com/johnnyh1975/ha_roomba_plus/issues)
with a [diagnostics download](#sending-a-diagnostics-download).

## Find your problem

**[Installing](#installing)**

- [The Marketplace does not offer the new release yet](#the-marketplace-does-not-offer-the-new-release-yet)
- [The Marketplace says hacs.json is missing](#the-marketplace-says-hacsjson-is-missing)
- [Adding Roomba+ fails with cannot import name 'RoombaClient' from 'roombapy'](#adding-roomba-fails-with-cannot-import-name-roombaclient-from-roombapy)
- [Error occurred loading flow for integration roomba in your log, or a Roomba card you cannot dismiss](#error-occurred-loading-flow-for-integration-roomba-in-your-log-or-a-roomba-card-you-cannot-dismiss)
- [A robot set up in two integrations (Home Assistant 2026.11 and later)](#a-robot-set-up-in-two-integrations-home-assistant-202611-and-later)

**[Setup & connection](#setup--connection)**

- ["Failed to connect" during setup](#failed-to-connect-during-setup)
- [Setup fails and a rest980 server is still running](#setup-fails-and-a-rest980-server-is-still-running)
- [The iRobot app loses connection when Roomba+ is running](#the-irobot-app-loses-connection-when-roomba-is-running)
- [A Classic robot was set up cloud-only](#a-classic-robot-was-set-up-cloud-only)
- [Setup cannot find the robot password](#setup-cannot-find-the-robot-password)
- [Replacing or selling your robot](#replacing-or-selling-your-robot)

**[The robot does not do what you asked](#the-robot-does-not-do-what-you-asked)**

- [I want to clean one zone on demand, and the app no longer lets me](#i-want-to-clean-one-zone-on-demand-and-the-app-no-longer-lets-me)
- [The robot accepts a room clean, then sits on the dock](#the-robot-accepts-a-room-clean-then-sits-on-the-dock)
- [smart_start queues forever / never starts](#smart_start-queues-forever--never-starts)
- [The robot accepts a command and does nothing](#the-robot-accepts-a-command-and-does-nothing)
- [Why does my robot keep stopping?](#why-does-my-robot-keep-stopping)
- [A paused mission disappeared on its own](#a-paused-mission-disappeared-on-its-own)
- [The robot will not drive home on a low battery](#the-robot-will-not-drive-home-on-a-low-battery)
- [I sent it to a room on another floor and it got lost](#i-sent-it-to-a-room-on-another-floor-and-it-got-lost)
- [The robot emptied its bin and I did not ask it to](#the-robot-emptied-its-bin-and-i-did-not-ask-it-to)

**[Map and rooms](#map-and-rooms)**

- [Smart Map zones not appearing (i/s/j-series)](#smart-map-zones-not-appearing-isj-series)
- [Zone management — changes not reflected in dropdown immediately](#zone-management--changes-not-reflected-in-dropdown-immediately)
- [clean_room says "rooms from different maps" after deleting the old map](#clean_room-says-rooms-from-different-maps-after-deleting-the-old-map)
- [clean_room or vacuum.clean_area raises no_valid_segments immediately after setup](#clean_room-or-vacuumclean_area-raises-no_valid_segments-immediately-after-setup)
- ["Map vacuum segments to areas" repair fires — what do I do?](#map-vacuum-segments-to-areas-repair-fires--what-do-i-do)
- ["Map vacuum segments to areas" dialog shows no rooms on the left](#map-vacuum-segments-to-areas-dialog-shows-no-rooms-on-the-left)
- [The live map does not match the room](#the-live-map-does-not-match-the-room)
- [Rooms are wrong, or fewer than expected](#rooms-are-wrong-or-fewer-than-expected)
- [cleaned_rooms is empty on a mission that definitely cleaned rooms](#cleaned_rooms-is-empty-on-a-mission-that-definitely-cleaned-rooms)
- [Room names show as boxes on the map](#room-names-show-as-boxes-on-the-map)

**[Sensors, status and maintenance](#sensors-status-and-maintenance)**

- [filter_last_replaced / brush_last_replaced shows Unknown](#filter_last_replaced--brush_last_replaced-shows-unknown)
- [Wear Intelligence sensors show Unknown](#wear-intelligence-sensors-show-unknown)
- [optimal_clean_window shows Unknown](#optimal_clean_window-shows-unknown)
- [Mission log sensors show Unknown after upgrading](#mission-log-sensors-show-unknown-after-upgrading)
- [last_error_code shows a stale error after the robot has recovered](#last_error_code-shows-a-stale-error-after-the-robot-has-recovered)
- [Recent cleaned area / cleaning time show lower values than expected](#recent-cleaned-area--cleaning-time-show-lower-values-than-expected)
- [Dock contact / Wi-Fi channel / optical dirt detection sensors aren't showing up at all](#dock-contact--wi-fi-channel--optical-dirt-detection-sensors-arent-showing-up-at-all)
- [Self-calibrating filter/brush thresholds: when do they activate?](#self-calibrating-filterbrush-thresholds-when-do-they-activate)
- [Mission anomaly detection](#mission-anomaly-detection)
- [The status says "No contact"](#the-status-says-no-contact)
- [The status says "Charging mid-mission"](#the-status-says-charging-mid-mission)

**[Schedules, presence and demand cleaning](#schedules-presence-and-demand-cleaning)**

- [demand_clean_blocked stays ON even though the robot is idle](#demand_clean_blocked-stays-on-even-though-the-robot-is-idle)
- [Demand cleaning never triggers despite being enabled](#demand-cleaning-never-triggers-despite-being-enabled)
- [Demand cleaning triggers too often / not often enough](#demand-cleaning-triggers-too-often--not-often-enough)
- [Presence-aware scheduling step not visible in options menu](#presence-aware-scheduling-step-not-visible-in-options-menu)

**[iRobot account, cloud and Prime robots](#irobot-account-cloud-and-prime-robots)**

- [Cloud authentication fails](#cloud-authentication-fails)
- [The iRobot account lists no robots](#the-irobot-account-lists-no-robots)
- [Cloud mission history not available for my Roomba 980](#cloud-mission-history-not-available-for-my-roomba-980)
- [No favourite buttons](#no-favourite-buttons)
- [A Prime robot that says it is cleaning and is not](#a-prime-robot-that-says-it-is-cleaning-and-is-not)
- [Mission history export](#mission-history-export)
- [Mission history import](#mission-history-import)

**[Reporting a problem](#reporting-a-problem)**

- [Before you send a diagnostics download](#before-you-send-a-diagnostics-download)
- [Sending a diagnostics download](#sending-a-diagnostics-download)

**[Older versions](#older-versions)**: problems fixed before 4.4, for anyone still running an older version.

---

## Installing

Installing and updating Roomba+ from the Marketplace or HACS, and the built-in Roomba integration.

From Home Assistant 2026.11, Roomba+ is installed and updated from the
**Marketplace** (Settings → Marketplace), which is HACS built into Home
Assistant. On older versions it is HACS. The catalog, the releases and
Roomba+'s `hacs.json` are the same for both.

### The Marketplace does not offer the new release yet

**Applies to:** Every robot

**Cause:** The Marketplace reads its catalog every 6 hours, and the catalog is rebuilt every 4, so a release can take most of a day to appear.  
**Fix:** Wait. For a beta, switch on **Include pre-releases** in the menu of the Roomba+ page.

The Marketplace reads its catalog every 6 hours, and the catalog itself is
rebuilt every 4 hours, so a release can take most of a day to appear. A
pre-release appears only if you switched on **Include pre-releases** in the
menu of the Roomba+ page, and only while it is newer than the latest stable
release.

### The Marketplace says `hacs.json` is missing

**Applies to:** Every robot

**Cause:** Not the file: it is in the repository root and every release carries the zip it names. Something underneath failed.  
**Fix:** Open an issue with the log lines around the message (Settings → System → Logs).

It is not: `hacs.json` is in the root of the repository, and every release
carries the `ha_roomba_plus.zip` it names. If you see this, open an issue with
the lines from your log around the message (Settings → System → Logs), so the
error underneath can be found.

### Adding Roomba+ fails with `cannot import name 'RoombaClient' from 'roombapy'`

**Applies to:** Classic robots · Home Assistant 2026.10 and earlier

**Cause:** Home Assistant's built-in Roomba integration loaded `roombapy` 1.9.1 first; Roomba+ needs 2.x, and Python keeps the first copy until the next restart.  
**Fix:** Update to Home Assistant 2026.11, or use the workaround in the next entry, restart, then add Roomba+.

The setup dialog stops with "Roomba+ needs the asynchronous `roombapy`
2.x", and restarting does not help, even after deleting the built-in
Roomba integration's entry (@gdenas).

**Why.** This is the other side of the conflict in the next section.
Home Assistant 2026.10 and earlier ship the built-in Roomba integration
with `roombapy` 1.9.1; Roomba+ needs 2.0.2. Python loads one copy of a
library per run, and the integration that loads it first keeps it until
the next restart.

When Home Assistant finds your robot on the network, it prepares the
built-in integration's setup dialog shortly after every restart, whether
or not that integration has an entry. Preparing it installs 1.9.1 and
loads it. Home Assistant logs installs only at info level, so the
default log shows nothing. When you then add Roomba+, Home Assistant
installs 2.0.2 again, but the old copy is already loaded and Roomba+
cannot import what it needs.

Once Roomba+ is set up the order reverses: configured integrations load
before discovery runs, so Roomba+ gets 2.0.2 and the built-in setup
dialog is the one that fails (next section). The two installs still
swap the library on disk at every restart.

**What to do.** Use the workaround in the next section ("A workaround,
if you do not use the built-in integration"), restart, then add
Roomba+.

**Home Assistant 2026.11** moves the built-in integration to `roombapy`
2.0.2, the version Roomba+ requires (checked on 2026.11.0). Both
integrations ask for the same library and neither replaces the other's,
so this no longer happens. After updating, the workaround folder can be
deleted. If the same robot is then set up in both integrations, see
[A robot set up in two integrations](#a-robot-set-up-in-two-integrations-home-assistant-202611-and-later).

If this error appears on 2026.11 or later all the same, the old library
comes from somewhere else: look in `custom_components` for an integration
that requires an older `roombapy`.

### `Error occurred loading flow for integration roomba` in your log, or a Roomba card you cannot dismiss

**Applies to:** Classic robots · Home Assistant 2026.10 and earlier

**Cause:** On Home Assistant 2026.10 and earlier the built-in Roomba integration cannot load next to Roomba+'s `roombapy` 2.x; discovery keeps trying.  
**Fix:** Nothing is broken. Leave it, silence the log, or remove the card with the workaround below. Home Assistant 2026.11 ends it.

Two symptoms of the same cause. In the log:

repeating every few minutes, alongside a blocking-call warning about
`homeassistant.components.roomba.config_flow`:

```
ERROR [homeassistant.components.roomba] Error occurred loading flow for
integration roomba: cannot import name 'Roomba' from 'roombapy'
```

**Home Assistant 2026.10 and earlier.** From 2026.11 the built-in
integration uses the same library as Roomba+, and neither symptom
appears there.

**Nothing is broken.** Roomba+ needs `roombapy` 2.x, which is
asynchronous; Home Assistant's built-in Roomba integration uses the
synchronous 1.x interface. Home Assistant installs one copy of the
library, so the built-in integration cannot import what it expects.

You see this even without that integration set up: Home Assistant finds
your robot on the network and tries to prepare the built-in setup
dialog, which is what fails. Your robot, and Roomba+, are unaffected.

In Settings → Devices & Services, a **Discovered** card for the
built-in Roomba integration keeps coming back. Opening it fails with:

```
Config flow could not be loaded: {"message":"Invalid handler specified"}
```

**Ignoring the card fails the same way (@FJSoninC).** Home Assistant
ignores a discovered device by starting the built-in integration's own
setup flow in "ignore" mode, and that flow is exactly what cannot be
loaded. So the card cannot be dismissed from the UI. The workaround
below is the only way to make it go away.

**What you can do, none of it required:**

*Leave it.* The card and the log line are cosmetic.

*Silence the log messages.* In `configuration.yaml`:

```yaml
logger:
  logs:
    homeassistant.components.roomba: fatal
```

This affects only the built-in integration's own logging. Nothing from
Roomba+ is hidden by it. The card stays.

*Remove the card:* only with the workaround below. With it in place the
built-in setup flow loads again (as an empty one), so the card can be
ignored, or no longer appears at all. An ignored entry does not trigger
the "Two Roomba integrations" notice.

**Why it is not simply fixed:** resolving it means the built-in
integration moving from the synchronous library to the asynchronous
one — a rewrite of that integration, not a version bump, and not ours
to make. Home Assistant made it in 2026.11.

#### A workaround, if you do not use the built-in integration

Contributed by @nareso, and it works for a reason worth understanding:
Home Assistant loads an integration from `custom_components/` in
preference to its own copy of the same domain. A folder named `roomba`
containing a manifest with **no `requirements`** therefore shadows the
built-in one, and nothing installs the old library.

`/config/custom_components/roomba/manifest.json`:

```json
{
  "domain": "roomba",
  "name": "Roomba Override",
  "version": "1.0.0",
  "integration_type": "device"
}
```

`/config/custom_components/roomba/config_flow.py`:

```python
from homeassistant import config_entries

class ConfigFlow(config_entries.ConfigFlow, domain="roomba"):
    """Dummy config flow for the overridden Roomba integration."""

    async def async_step_user(self, user_input=None):
        return self.async_abort(reason="not_supported")
```

Restart Home Assistant afterwards.

**Read this part before you do it.** You are replacing a core
integration with an empty one:

- The built-in Roomba integration becomes unavailable. If you have
  entities from it, they stop working. Only do this if you use Roomba+
  for every robot.
- Home Assistant will log that you are running a custom integration it
  has not tested. That warning is correct and expected here.
- It is yours to maintain. Delete the folder to undo it, and check it
  still behaves after Home Assistant upgrades.
- Roomba+ neither ships nor requires this. Deleting the built-in
  integration's entry through Settings stops it running, but not its
  discovery. This is for people who would rather not have it reappear
  through discovery, or who cannot add Roomba+ because of it (previous
  section).

### A robot set up in two integrations (Home Assistant 2026.11 and later)

**Applies to:** Classic robots · Home Assistant 2026.11 and later

**Cause:** From 2026.11 the built-in integration loads next to Roomba+, and both take the robot's one local connection from each other.  
**Fix:** Remove the robot from one of the two.

Roomba+ shows this notice when a robot it runs is also set up in Home
Assistant's built-in Roomba integration.

**Why it is new.** Until 2026.10 the built-in integration could not load
next to Roomba+ (the two sections above), so an old entry of it sat there
and did nothing. From 2026.11 both use the same `roombapy` and both load,
so both connect to the robot. A robot takes one local connection at a
time, and the two take it from each other.

**What to do.** Remove the robot from one of the two. Removing the
built-in integration's entry changes nothing in Roomba+. A different
robot in each integration is fine, and gets no notice.

---

## Setup & connection

Adding a robot, its local connection, and moving a robot.

### "Failed to connect" during setup

**Applies to:** Classic robots

**Cause:** Some robots answer the pairing only while they run, or something else holds their one local connection.  
**Fix:** Press **Clean** on the robot and retry at once; stop anything else connected to it (next entry); or set up with the iRobot account.

Press the physical **Clean** button on the robot to start a manual cleaning job, then immediately retry credential retrieval in HA. Some models only respond while actively running.

If automatic pairing fails entirely: → [Retrieve iRobot credentials manually](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

### Setup fails and a rest980 server is still running

**Applies to:** Classic robots

**Cause:** The robot takes one local connection at a time, and the rest980 server holds it.  
**Fix:** Restart Home Assistant once with Roomba+ installed and the server still running (for the room import), then stop the rest980 container and set Roomba+ up.

The robot accepts **one local connection at a time**, and the rest980
server holds it on port 8883 for as long as its container runs. Roomba+
then cannot connect at all, and the setup error says nothing about why.

**Stop the rest980 container before setting Roomba+ up.** The
roomba_rest980 *integration* can stay installed — that is what the room
import reads from, and it is a different thing from the server.

**But restart after installing Roomba+ first, while the container is still running.**
roomba_rest980 builds its room entities during setup from data it fetches
off the server; with the container stopped its config entry fails to load
and those entities never appear, so there is nothing left to import from.
Existing entities are safe — the room data sits on the entity and survives
the server going away. It is only the next start that cannot recreate them.
See [Moving to Roomba+](MIGRATING.md#from-roomba_rest980).

This is the same constraint behind the entry below and behind "mqtt
slot" errors: **one slot, and whoever holds it wins.** A robot that
refuses a connection is usually not broken; something else is already
connected to it.

### The iRobot app loses connection when Roomba+ is running

**Applies to:** Classic robots

**Cause:** The robot allows one local connection, and Roomba+ holds it.  
**Fix:** Nothing to fix: the app uses the cloud meanwhile. To free the connection for something else, disable or reload the Roomba+ entry.

Expected — the robot only allows one local MQTT connection, and Roomba+ holds it. The iRobot app uses the cloud path meanwhile, which works normally.

To hand the connection to something else, a rest980 container during a migration say, disable or reload the Roomba+ entry.

### A Classic robot was set up cloud-only

**Applies to:** Classic robots set up from an iRobot account

**Cause:** Its model was not known as Classic when it was added from the account, so it took the Prime path.  
**Fix:** Open the entry → **Reconfigure**, enter the robot's IP address and leave the password empty. Remove the unavailable cloud-only entities afterwards.

A Combo j7+ or j9+ added from the iRobot account before 4.4.4 was set up
as a cloud-only robot. To move one that already exists: open its entry, choose **Reconfigure**, enter
the robot's IP address and leave the password empty. Roomba+ takes the
password from your iRobot account, checks the local connection and
switches the entry to it. The entry, its options and its account stay.
The entities of the cloud-only setup stay behind as unavailable; remove
them from the device page.

The same works for any Classic robot that ended up cloud-only. A Prime
robot has no local connection, so for it the check fails and nothing
changes.

### Setup cannot find the robot password

**Applies to:** Classic robots

**Cause:** The robot did not hand out its password on the local network.  
**Fix:** Choose **Set up with my iRobot account**: the password comes from the account, for any model.

Choose **"Set up with my iRobot account"**. It is required for
Prime robots, which have no local setup, and it is also the right
choice for any robot whose password could not be retrieved
automatically, whatever its model.

A robot found on your network needs no password at all when
another robot of the same iRobot account is already running here: the
password comes from the account.

### Replacing or selling your robot

**Applies to:** Every robot

**Cause:** The entry, its history and what Roomba+ learned belong to the old robot.  
**Fix:** Back up, then delete the entry: [Replacing or selling a robot](MIGRATING.md#replacing-or-selling-a-robot).

Back up first, then delete the entry; the steps, and how to carry the mission history over to
a new robot, are in [Moving to Roomba+ → Replacing or selling a robot](MIGRATING.md#replacing-or-selling-a-robot).

---

## The robot does not do what you asked

A start, a room clean or a command that is accepted and then nothing happens, or something else happens.

### I want to clean one zone on demand, and the app no longer lets me

**Applies to:** Prime robots

**Cause:** The iRobot app dropped zone favourites; it offers rooms only.  
**Fix:** Use `roomba_plus.clean_zone` with the zone's name or id.

The iRobot app dropped zone favourites — rooms only. Use
`roomba_plus.clean_zone` instead:

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

### The robot accepts a room clean, then sits on the dock

**Applies to:** Smart Map robots

**Cause:** Error 224: the robot could not place itself on the map it was asked to clean, usually because it stands on another floor.  
**Fix:** Carry it to the map's floor first; pick the right map (the zone select of that map on Classic, the **Map** entity on Prime). With one map, let the robot re-learn it in the iRobot app.

The log shows the command accepted and no mission started, with
`error=224` — a localisation failure. The robot could not work out
where it was on the map it was asked to clean.

**If you have more than one map**, that is the usual cause: a robot can
only clean the map it is standing on. A room or zone command goes to the
map the room belongs to, so a robot standing on another floor, or
outside the area a map covers, answers with 224. Carry it there first.
Roomba+ cannot check this beforehand on a Classic robot, which does not
report the map it is on; the log names the map the robot was last sent
to when the two differ.

- **Classic:** every map has its own zone select. The one of a map that
  is not the most recently edited is created disabled and named
  "(inactive)"; enable it to pick rooms of that map. The rooms map
  shows the map of the last command.
- **Prime:** the **Map** entity. Leave it on "follow robot" unless you
  deliberately want a different map; picking one overrides what the
  robot reports.

**If you have one map**, 224 means the robot genuinely could not
localise: it may have been moved while docked, or the map may need
re-learning in the iRobot app.

### `smart_start` queues forever / never starts

**Applies to:** Every robot with blocking sensors

**Cause:** A blocking sensor is still on.  
**Fix:** Check the sensors in `binary_sensor.<robot>_start_blocked`'s `blocking_entities`; react to `roomba_plus_start_timeout` to alert or retry.

Check that the blocking sensors are reporting correctly. Unavailable or unknown sensors are treated as non-blocking. If the queue expires, `roomba_plus_start_timeout` is fired — automate on this event to alert or retry.

### The robot accepts a command and does nothing

**Applies to:** Every robot

**Cause:** The robot took the command and could not act on it: a cliff sensor fired, it is away from its dock without a camera, it is paused, or its state is frozen.  
**Fix:** Read the readiness sensor and the warning Roomba+ logs about 20 seconds later; then move it off the dark rug, send it home first, resume before a room clean, or power-cycle it.

No error, no phase change, no mission. The command appears to have been
received — and in fact it was.

Home Assistant logs a warning about twenty seconds later saying no
mission started, with the readiness value. Three situations
produce this:

**The readiness sensor reads "Cliff".** A cliff sensor has decided the
floor is a drop-off, and the robot will not move until it is physically
picked up and put somewhere else. A reboot does not clear it — the
sensor reads the same surface again within a minute.

This does not need an actual drop-off. **Dark patterned rugs trigger it**
— reported on an i3+, reproducible on the same rug every time. If the
robot is stopped on a dark floor and refuses every command, move it by
hand.

**A room clean sent while the robot is away from its dock.** Cameraless
i-series robots (i3, i3+) use the dock as their localisation reference.
Away from it the robot does not know where it is relative to the map, so
it cannot navigate to a region — only a plain whole-house clean works.
The official iRobot app behaves the same way.

Send the robot home first, or use a plain start.

**A stale map version.** Rare, and the integration resolves the map
version at send time to avoid it.

**A room clean sent while the robot is PAUSED**. A paused
robot refuses a region-targeted start and takes `resume` from the same
state instantly. This is not about localisation: it was tested
deliberately on a robot with its best localisation reading in weeks, and
it still refused. Resume the mission first, or stop it and send the room
clean from idle.

> ⚠️ **You will get no warning for this one.** The twenty-second watcher
> asks whether a mission started, which is only the right question when
> none was running — a paused robot already has an open cycle before and
> after the command, so a swallowed one looks exactly like success.
> Detecting it needs a signature of what acceptance looks like, and no
> accepted region start from a paused robot has ever been recorded to
> compare against.

**The robot has been ignoring everything for hours or days.** A different
fault that looks identical from Home Assistant, and it has its own
section: see [A Prime robot that says it is cleaning and is
not](#a-prime-robot-that-says-it-is-cleaning-and-is-not). The tell is
`phase` reading `stale`, and only a power cycle clears it.

### Why does my robot keep stopping?

**Applies to:** Every robot

**Cause:** The robot keeps its own reasons for the last ten runs that ended.  
**Fix:** Read `recent_pause_reasons` and `most_frequent_pause_reason` on the error sensor.

The error sensor carries two attributes the robot itself provides:

- **`recent_pause_reasons`** — the reasons the last ten runs ended, as
  the robot's own codes
- **`most_frequent_pause_reason`** — the commonest of them, named

A robot that has aborted four of its last ten runs for the same reason
is telling you something a single incident does not. There is no
threshold attached: nobody has enough field data yet to say what counts
as a lot, and a warning without one would be a guess.

### A paused mission disappeared on its own

**Applies to:** Every robot

**Cause:** Pausing sets an expiry, 90 minutes on the firmware measured; then the robot drops the mission.  
**Fix:** Resume or dock before the mission expiry sensor runs out. Pausing is not a way to park a robot.

Pausing sets an expiry, and the robot drops the mission when it runs
out. **Ninety minutes** on the firmware this was measured on
(`daredevil 2.6.0`, i3+) — the robot sets `expireTm` to now plus 5400
seconds the moment you pause.

After that the mission is gone, not paused. Resuming does nothing
because there is nothing to resume.

The mission expiry sensor shows the countdown, so a paused robot tells
you how long it will wait. A paused robot also drains at roughly
0.3% battery per minute, which over ninety minutes is most of a
charge — pausing is not a way to park a robot.

Measured by @Young9898 on a dedicated test unit. Other firmwares may
use a different window; the sensor reads the robot's own value rather
than assuming ninety minutes.

### The robot will not drive home on a low battery

**Applies to:** Every robot

**Cause:** Below some charge (about 21 % on the one robot measured) the robot refuses `dock` as well as starts.  
**Fix:** Recall it on low battery before that point, not at it.

Below a certain charge the robot refuses **`dock` as well as mission
starts** — so a robot stranded mid-floor will not accept the one command
you most want it to take. The readiness sensor reads *Insufficient
charge*.

**If you automate a recall on low battery, it has to fire before the
threshold**, not at it.

Where the threshold sits is not known. Every recorded occurrence was at
or below roughly 21% on one i3+ (`daredevil 2.6.0`), but whether that is
a fixed percentage, computed against the distance home, or subject to
hysteresis is unmeasured — and whether `start` and `dock` share the same
gate is unknown too. Treat 21% as the only number anyone has seen rather
than as the boundary.

Observed by @AlakazipLabs across two weeks of lossless shadow logs.

### I sent it to a room on another floor and it got lost

**Applies to:** Robots with several maps

**Cause:** A robot cleans only the map it stands on; elsewhere it searches for landmarks that are not there (Prime: error 69).  
**Fix:** Carry it to that floor first, or use a favourite made for that map in the iRobot app.

Expected, and not something this integration can prevent.

A robot can only be on one floor at a time. Asking it to clean a room on
a map it is not standing on means asking it to find landmarks that are
not there: it drives, searches, fails to place itself, and stops. On
Prime robots that surfaces as a navigation error — @chairstacker saw
about three minutes of wandering, then "Roomba is a little lost" and
**error 69**.

**Why the command is not blocked.** This integration knows which map the
robot last *reported*, and that value is routinely stale. Another tester
ran a favourite for a map we did not consider active and the robot went
straight there — because it was physically on that floor, whatever the
flag said. Refusing on a stale flag would block missions that work.

A mismatch is written to the Home Assistant log when the command is
sent, naming both maps, so an unexplained navigation error a
few minutes later has something to connect it to.

**What actually works across floors:** carry the robot to that floor
first, or use a **favourite** created in the iRobot app for that map.
Favourites carry their own map, which is what makes them the shortest
route to a one-press control for another floor.

**On Prime error numbers.** Prime robots report a different error
namespace from the older Classic ones — the same number means different
things on the two, with no overlap at all across the sixteen codes they
share. Rather than show a Classic label that would be wrong, Roomba+
shows the raw code. `Error 69` is the robot's own number for this.

### The robot emptied its bin and I did not ask it to

**Applies to:** Robots on a Clean Base

**Cause:** A `dock` command to a robot already on a Clean Base empties the bin.  
**Fix:** Check automations that send `vacuum.return_to_base` while the robot is home.

Most likely you sent it home while it was already home.

A `dock` command means two different things depending on where the robot
is. Away from the dock it is a recall. **On the dock it is an
evacuation** — the robot runs its bin-empty cycle and settles back to
idle, about 22 seconds end to end.

That is the robot's own behaviour, not something this integration adds.
It was established by a deliberate test: one `dock` command to a robot
sitting at 100% on its dock, nothing sent before or after, every packet
captured (@AlakazipLabs, i3).

So `vacuum.return_to_base` — the **Return to dock** button, or the
service in an automation — will empty the bin if the robot is already
there. Home Assistant has no separate notion of "go home" versus "empty
now", so pressing it twice gives you two evacuations.

**What this integration deliberately does not do** is suppress the
command when it thinks the robot is docked. Deciding that would mean
trusting the reported activity, and a wrong guess would swallow a real
recall — a robot left stranded mid-floor is a worse outcome than an
unwanted bin empty.

**If your bin empties more often than you expect**, an automation
sending `return_to_base` on a schedule or at the end of a mission is the
first thing to check. Robots with a Clean Base also have their own
`autoevac_frequency` setting, which iRobot's own service has been known
to ignore.

---

## Map and rooms

The live map, room detection, room names, and cleaning by room or area.

### Smart Map zones not appearing (i/s/j-series)

**Applies to:** Smart Map robots (i, s, j series)

**Cause:** Without the iRobot account, zones come from positions the robot sends, and many Smart Map robots send none.  
**Fix:** Add the iRobot account: the names come from the cloud. The diagnostics' `position` says whether the robot sends positions.

Check the diagnostics download for `"position"` carrying actual
coordinates, **not** `"cap": {"pose": ...}`.

`cap.pose` is a compile-time constant on lewis firmware — it says
nothing about whether a robot publishes a position. Four robots across
three firmware families report `pose: 2` and have never sent one; a
900-series reports `pose: 1` and sends them continuously. The split
looks like cause and is a correlation with model generation. If cloud credentials are configured, zone names come directly from the cloud and the naming repair flow is suppressed.

### Zone management — changes not reflected in dropdown immediately

**Applies to:** Classic robots

**Cause:** The select refreshes with the robot's next message.  
**Fix:** Wait a few seconds while the robot is active.

Alias and hidden changes are written immediately, but the zone select dropdown may take one MQTT message cycle to refresh. Typically resolves within seconds when the robot is active.

### `clean_room` says "rooms from different maps" after deleting the old map

**Applies to:** Smart Map robots with an iRobot account

**Cause:** The iRobot cloud still reports the deleted map, for up to 24 hours.  
**Fix:** Save the cloud credentials step under Configure again to refresh at once.

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

### `clean_room` or `vacuum.clean_area` raises `no_valid_segments` immediately after setup

**Applies to:** Smart Map robots with an iRobot account, Prime robots

**Cause:** The first cloud fetch has not finished, so no rooms are known yet.  
**Fix:** Wait a few minutes and retry. If it stays, check `region_count_active` in the diagnostics.

The active pmap ID may not yet be available — typically happens on fresh install or immediately after adding cloud credentials before the first cloud fetch completes. Wait a few minutes and retry. If it persists, check that `region_count_active` in diagnostics is > 0.

### "Map vacuum segments to areas" repair fires — what do I do?

**Applies to:** Smart Map and Prime robots · Home Assistant 2026.3 and later

**Cause:** Home Assistant asks once which room belongs to which area, for `vacuum.clean_area`.  
**Fix:** Map the rooms (vacuum entity → ⚙ → *Map vacuum segments to areas*), or dismiss it if you use `clean_room` only.

This is a one-time HA setup step for `vacuum.clean_area` (HA 2026.3+). Open the vacuum entity → ⚙ Entity settings → "Map vacuum segments to areas", match each robot room to a Home Assistant area, and save. Full instructions: [Clean by Home Assistant area](guides/clean-rooms.md#clean-by-home-assistant-area).

If you don't use `vacuum.clean_area`, dismiss the repair — `roomba_plus.clean_room` works without it.

### "Map vacuum segments to areas" dialog shows no rooms on the left

**Applies to:** Smart Map and Prime robots · Home Assistant 2026.3 and later

**Cause:** Roomba+ has no rooms to offer yet: no account, no Smart Map, no saved map, or the first fetch is not done.  
**Fix:** Work through the list below.

The left column is populated from the iRobot cloud, so an empty list means the integration has no room data to offer yet. Check, in order:

- **Cloud credentials configured?** Room segments require the cloud coordinator (Settings → Configure → iRobot cloud credentials). Local-only setups have no room names to map.
- **SMART robot?** Only i/s/j-series robots expose cloud rooms. 900-series (EPHEMERAL) robots have no cloud pmap, so they list no segments here and don't support room- or area-targeted cleaning at all (`clean_room` and `vacuum.clean_area` are SMART-only) — they clean the whole floor.
- **Map finalized in the iRobot app?** If rooms aren't named/saved in the iRobot app, the cloud returns none. Open the app, confirm the room layout, then re-save the cloud credentials step in Configure to force a coordinator refresh.
- **First fetch completed?** On a fresh install, wait a few minutes for the first cloud poll, then reopen the dialog. Check `region_count_active` in the diagnostics download is > 0.

### The live map does not match the room

**Applies to:** 900-series and Smart Map robots

**Cause:** The path breaks where the robot's position jumped (a relocalisation or a pickup), and a stretch it cannot place is left out of the stored map.  
**Fix:** Nothing to fix. Frequent gaps during ordinary cleaning are worth reporting.

Two things are deliberate.

**Gaps in the trail.** The path now breaks where the robot's reported
position jumped further than it could have driven: a relocalisation, or
someone picking it up. A line drawn straight through would assert travel
that did not happen. A gap is the honest picture
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

### Rooms are wrong, or fewer than expected

**Applies to:** 900-series

**Cause:** Areas come after three missions and follow coverage, not walls; an uncrossed doorway reads as a wall.  
**Fix:** Let it run more missions through the doorways; give two areas the same name to make one room (Zone management).

**Rooms need three completed missions.** Nothing appears before that,
and this is on purpose — see [Room detection](explanations/how-rooms-are-found.md)
for why an early map produces rooms that later disappear.

**The shape follows coverage, not walls.** Room outlines are built from
where the robot drove, so furniture leaves holes and edges fall short of
the wall by roughly the robot's radius. Boundaries land at narrow points
in the coverage, which usually but not always means a doorway. More
missions improve the count; they do not change the shape.

If a room is split that should be one, check whether the doorway between
them has actually been driven through — an uncrossed threshold reads as
a wall. Merging is available in the Options Flow.

### `cleaned_rooms` is empty on a mission that definitely cleaned rooms

**Applies to:** Robots with an iRobot account

**Cause:** The cloud's account of the mission has not arrived yet.  
**Fix:** Wait a few minutes after docking. Consistently empty for one robot: report it with a diagnostics download.

That field is built from the cloud's own account of the mission
(`timeline.finEvents`). Empty means **the cloud data has not arrived**,
not that no rooms were cleaned — the two look identical from outside.

Enrichment usually lands within a few minutes of docking. If it is
consistently absent for one robot while another on the same account gets
it, that is worth reporting with a diagnostics download: it now records
whether each stored mission carries a timeline at all.

### Room names show as boxes on the map

**Applies to:** Every robot with a map

**Cause:** The map font has no Japanese, Chinese or Korean characters, and Roomba+ ships none.  
**Fix:** Put a font with your script into `roomba_plus/fonts` in the configuration folder and restart.

The maps draw their labels with DejaVu Sans, which has no Japanese,
Chinese or Korean characters; Roomba+ does not ship a font for them,
which would add 5 to 20 MB to every installation. A label falls back to
the first font that has all its characters:

1. Download a font with your script, for example **Noto Sans JP**,
   **Noto Sans SC/TC** or **Noto Sans KR** (free, SIL Open Font License),
   as a `.ttf`, `.otf` or `.ttc` file.
2. Put it into the folder `roomba_plus/fonts` in your Home Assistant
   configuration folder (the one with `configuration.yaml`; create the
   folder), for example `/config/roomba_plus/fonts/NotoSansJP-Regular.ttf`.
3. Restart Home Assistant.

The folder is outside the integration, so an update keeps the font.
Fonts the system already has (Noto CJK, WenQuanYi, IPA, Nanum) are used
too; Home Assistant OS has none. Until a font is there, the log says once
which characters it cannot draw. The diagnostics download lists the fonts
it found under `map_fallback_fonts` (file names only).

---

## Sensors, status and maintenance

Sensors that show Unknown or an unexpected value, the status, and the maintenance countdowns.

### `filter_last_replaced` / `brush_last_replaced` shows Unknown

**Applies to:** Classic robots

**Cause:** Nothing has been reported replaced yet.  
**Fix:** Mark the part done once (button, list or `roomba_plus.maintenance_done`).

These sensors are Unknown until the first reset is performed. Press the reset button on the device page or call the reset action to populate them.

### Wear Intelligence sensors show Unknown

**Applies to:** Classic robots

**Cause:** A wear rate needs 3 days of missions since the last reset.  
**Fix:** Wait; they fill on their own.

Wear sensors need at least 3 days of mission data since the last reset to calculate a meaningful rate. They populate automatically.

### `optimal_clean_window` shows Unknown

**Applies to:** Robots with presence scheduling on

**Cause:** It needs 5 completed missions since setup.  
**Fix:** Wait; it updates after the next mission end.

Requires at least 5 completed missions since integration setup. Updates automatically within minutes of the next mission end once 5 missions have recorded.

### Mission log sensors show Unknown after upgrading

**Applies to:** Every robot

**Cause:** The mission log fills from now on.  
**Fix:** Wait for the first finished mission.

The mission log is populated going forward only. Streak, completion rate, and area sensors will be Unknown until the first mission completes after upgrading — this is expected.

### `last_error_code` shows a stale error after the robot has recovered

**Applies to:** Every robot

**Cause:** The last error stays until a mission ends well.  
**Fix:** Run a mission; if it stays, restart Home Assistant.

The error state clears automatically when the next mission completes successfully. If it persists, restart HA to force re-reading the mission log from storage.

### Recent cleaned area / cleaning time show lower values than expected

**Applies to:** Classic robots with an iRobot account

**Cause:** iRobot's cloud gives about the last 30 missions, not a lifetime total.  
**Fix:** Nothing to fix; `source: recent_mission_window` says so. *Total missions* is a lifetime count.

These sensors aggregate data from the iRobot API window (~30 recent missions). The iRobot API does not expose a lifetime accumulator for area or time — the `source: recent_mission_window` attribute documents this. The **total missions** sensor is different: it reads the lifetime counter embedded in every cloud record.

### Dock contact / Wi-Fi channel / optical dirt detection sensors aren't showing up at all

**Applies to:** Classic robots

**Cause:** They are created disabled, and some exist only on some series.  
**Fix:** Device page → entities → filter *Diagnostic* → enable them.

These (and most other diagnostic-category sensors) are disabled by default to keep the entity list manageable — they don't show as `Unknown`, they simply aren't enabled. Go to the device page → entity list → filter by "Diagnostic" → enable the ones you want. Availability also depends on your robot's series: navigation landmark quality is 9-series only, optical/piezo dirt detection and dock contact counters are i/s-series only.

### Self-calibrating filter/brush thresholds: when do they activate?

**Applies to:** Classic robots

**Cause:** An interval is learned after two replacements of the part.  
**Fix:** Until then the series value or your hour option applies; `learned_maintenance` in the diagnostics shows what was learned.

After two or more resets of a given component, Roomba+ computes the median interval between resets and uses that as the effective threshold. Until two resets have been performed, the configured threshold is used. The learned values are visible in diagnostics under `learned_maintenance`.

### Mission anomaly detection

**Applies to:** Classic robots

**Cause:** The baseline is your robot's usual mission, so a single-room clean can look unusual.  
**Fix:** Nothing to do: it takes two unusual missions in a row, and clears with the next normal one.

**"Unusual cleaning patterns" Repair Issue fires for normal short cleans**

The anomaly detection uses your robot's personal performance history as the baseline. If the flag fires for a normal targeted single-room clean, the single-room area is far smaller than your typical full-home baseline — which is technically correct. The issue self-resolves: if the next mission is normal, the counter resets and the issue clears. Two consecutive anomalous missions are required to fire the issue.

### The status says "No contact"

**Applies to:** Classic robots

**Cause:** Nothing has arrived from the robot for over an hour: flat battery off the dock, a dock without power, or out of Wi-Fi range.  
**Fix:** Put it on a powered dock in Wi-Fi range. *Connected* says the same within five minutes.

The robot has not sent anything for over an hour. Every other entity is
still showing whatever it last reported, which can be days old — a
battery percentage from before the robot went quiet looks exactly like a
live one.

Usual causes: the robot is off its dock with a flat battery, the dock has
no power, or it is out of Wi-Fi range. The connectivity binary sensor
tells you the same thing sooner, within five minutes; the status waits an
hour so a brief dropout does not rewrite it mid-mission.

### The status says "Charging mid-mission"

**Applies to:** Every robot

**Cause:** The robot went back to top up and will carry on.  
**Fix:** Nothing to fix.

Normal. The robot returned to top up and will resume. It is distinguished
from plain charging because an automation reacting to "docked" or
"charging" would otherwise fire in the middle of a clean.

---

## Schedules, presence and demand cleaning

Presence-aware scheduling, demand cleaning and their options.

### `demand_clean_blocked` stays ON even though the robot is idle

**Applies to:** Robots with demand cleaning on

**Cause:** One of the four gates is closed.  
**Fix:** `blocking_reason` names it: the robot's state, a blocking sensor, presence, or the 6-hour gap.

Check all four gates: (1) robot cycle state — `vacuum.{name}` must be `docked` or `idle`; (2) blocking sensors — any configured blocking sensor is ON; (3) presence — all tracked persons must be away if presence mode is `away_only`; (4) minimum gap — 6 hours must have elapsed since the last demand trigger. The `blocking_reason` attribute on the sensor names the active gate.

### Demand cleaning never triggers despite being enabled

**Applies to:** Robots with demand cleaning on

**Cause:** A gate is closed, or there is no dirt baseline yet.  
**Fix:** Check `binary_sensor.<robot>_demand_clean_blocked`; on Classic make sure the iRobot account is added and some missions are recorded.

Check `binary_sensor.{name}_demand_clean_blocked` — it shows whether the robot is busy, a blocking sensor is active, or the 6-hour minimum gap has not elapsed. Also ensure cloud credentials are configured and at least 5 cloud mission records exist (check diagnostics for `region_count_active`).

### Demand cleaning triggers too often / not often enough

**Applies to:** Robots with demand cleaning on

**Cause:** The trigger multiplier sets how much dirtier than the weekday's usual level counts.  
**Fix:** Under Configure → *Demand cleaning*, lower the multiplier for more cleans, raise it for fewer.

Adjust the trigger multiplier in Configure → Demand cleaning. `1.5` (default) fires when dirt density is 50 % above the baseline for today's weekday. Lower the multiplier for more frequent triggers; raise it to require dirtier conditions. The baseline is weekday-specific — Monday's threshold is set by Monday's history — so the multiplier applies relative to each day's normal level.

### Presence-aware scheduling step not visible in options menu

**Applies to:** Every robot; the step depends on the model

**Cause:** The step needs a robot that can hold its schedule: Smart Map robots, Braava and Prime.  
**Fix:** Nothing to fix on a 900- or 600-series: they have no schedule hold.

The presence scheduling step appears for Classic robots that report `schedHold` (i, s, j, Braava m6) and for Prime robots. It does not appear for 900-series or 600-series robots.

**Prime robots take a different route to the same result.** A Prime robot carries `schedHold` in its
shadow, accepts a write to it, and runs anyway — iRobot's own app has no consumer for the field at
all. Presence scheduling therefore disables each schedule instead, which is what the app does. One
visible difference: a paused Classic schedule still looks scheduled in the iRobot app, while a
paused Prime one looks switched off. It is — but it is not what Classic does.

---

## iRobot account, cloud and Prime robots

Signing in, the cloud data, and what is particular to Prime robots.

### Cloud authentication fails

**Applies to:** Every robot with an iRobot account

**Cause:** The message says which: a rejected login, a locked account, too many sessions, a certificate problem, or iRobot's cloud.  
**Fix:** Follow the line for your message below; with a locked account, wait rather than retry.

The message says which of these it is, in your language:

- **The login was rejected:** check the email address and password of your iRobot account.
- **The account is temporarily locked:** do *not* re-enter your password. The lock clears by itself, usually within minutes, and every new attempt can extend it. Disable the Roomba+ entries of that account, wait about ten minutes, then enable them again.
- **Too many active sessions:** close the iRobot app on all devices and wait a few minutes before retrying.
- **Home Assistant cannot check iRobot's certificate:** the certificate store of the system Home Assistant runs on is missing or out of date. Waiting does not fix this one; updating the system's CA certificates does.
- **iRobot's certificate has expired**, or **iRobot's cloud has a problem of its own:** on iRobot's side, usually fixed within hours.

All robots on one iRobot account share one cloud login, so a failed login is tried once for all of them, not once per robot.

**After changing the iRobot password**, every robot of the account asks for the new one. Enter it for any one of them: the others that had the same old password take the new one and restart by themselves.

### The iRobot account lists no robots

**Applies to:** Every robot with an iRobot account

**Cause:** The robot is on another account, or you signed in with a different one.  
**Fix:** Follow the line for your message below.

The login works, but the setup ends without a robot to pick. The message
says which case it is:

- **The robot is registered to another iRobot account.** Usual with a
  second-hand robot that the previous owner never removed. Remove it from
  that account in the iRobot app (or ask the previous owner to), add it
  to your own account, and run the setup again. Do not retry the setup
  over and over in the meantime: every attempt is a login, and nothing
  changes until the robot is moved.
- **The account returned no robots.** Check that you signed in with the
  account the robot was added to in the iRobot app. The country is an
  unlikely cause: every country checked so far reaches the same iRobot
  servers.
- **The cloud lists robots the login did not return.** Not seen on a
  real account yet. Please open an issue with a debug log of the setup
  attempt; it contains only counts, no robot ids.

"Every robot is already configured" means exactly that: each robot of
the account already has its entry here.

### Cloud mission history not available for my Roomba 980

**Applies to:** 900-series with an iRobot account

**Cause:** The cloud history was not fetched.  
**Fix:** Open Configure → *iRobot cloud credentials*, save it again, then restart Home Assistant.

Go to Settings → Roomba+ → Configure → iRobot cloud credentials, re-save your credentials, then restart HA.

### No favourite buttons

**Applies to:** Prime robots

**Cause:** Prime favourite buttons are off by default, or none reached the integration.  
**Fix:** Configure → Connection settings → *Create a button for each saved favorite*; the diagnostics' `favourites` block says which reason applies.

Favourites from the iRobot app appear as one button each on Prime robots when *Create a button for each saved favorite* is on (Configure → Connection settings). If they are missing,
**download diagnostics** — the `favourites` block reports how many reached the integration and
whether the buttons are switched on, which tells apart the two reasons they could be absent.

A count of zero on an account that has favourites is worth reporting.

### A Prime robot that says it is cleaning and is not

**Applies to:** Prime robots

**Cause:** A mission that ended in an error left the robot's cloud document at `run`; every command is accepted and ignored.  
**Fix:** Power-cycle the robot. `phase` reading `stale` is the sign.

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
wording as attributes, so an automation can say what went wrong rather than quoting a number;
`help_url` links iRobot's article for the error. Error
48 reads "An obstacle blocked the entrance to a room" — on one tester's robot that single code
accounted for 93 of 111 timeline errors and every incomplete mission in the archive.

### Mission history export

**Applies to:** Every robot

**Cause:** Moving the history to another installation, or keeping a copy.  
**Fix:** Use the export below; `create_backup` saves everything Roomba+ stores for the robot.

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

### Mission history import

**Applies to:** Every robot

**Cause:** Bringing an exported history back.  
**Fix:** Post it as below; it is safe to repeat.

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

## Reporting a problem

What to send with a report, and what is in it.

### Before you send a diagnostics download

**Applies to:** Every robot

**Cause:** Credentials and robot ids are removed; room names, schedules, map geometry and history are not.  
**Fix:** Look it over; take it while the problem is still there.

Credentials and your BLID are removed automatically. Room names, zone names,
schedules, map geometry and mission history are **not** — the download is
useless without them. [What is in it, in full →](DATA_PRIVACY.md)

A partial download is more useful than none: send the block that matters if
the rest gives you pause.

**If a command seemed to do nothing**, three fields answer most of it, and
a download taken *while it is still broken* is worth far more than one
taken after a restart:

- `map.sent_commands` — what Roomba+ actually sent, when, and which rooms
  or zones it named
- `push_freshness` — how long since the robot last said anything. "No push
  message since startup" after a mission means nothing is arriving
- `map.active_map_versions` and `smart_map.last_command_summary` — which
  map a command named, which is what a localisation failure turns on

### Sending a diagnostics download

**Applies to:** Every robot

**Cause:** The download is what a report needs.  
**Fix:** Device page → ⋮ → **Download diagnostics**; during a mission for navigation or map questions.

Settings → Devices & Services → Roomba+ → the three dots → Download
diagnostics. Sensitive values are redacted before the file is written.

**Pull it during a mission if the question is about navigation or the
map.** The robot's navigation telemetry — pose confidence, kidnap
detection, landmark counts, map state — only fills while it is cleaning.
On the dock most of it reads zero, so a download taken afterwards looks
identical to a robot that does not report those fields at all.

For anything else — entity states, settings, mission history, dock
identity — the timing does not matter.

---

## Older versions

Problems that a version before 4.4 had, kept for anyone still running one.

<details>
<summary>Show the entries for older versions</summary>

### HACS shows only `main`, and downloading it hangs

**Applies to:** Every robot · HACS before Home Assistant 2026.11 · fixed by 4.0.0

The log carries a 404:

```
Download failed - Got status code 404 when trying to download
.../releases/download/main/ha_roomba_plus.zip
```

**Fixed by v4.0.0.** Update normally — no beta channel, no manual download.
4.0.0 is a normal release rather than a pre-release, so it is the newest thing
HACS can see and offer, and the fallback that produced the 404 no longer
happens.

**Why `main` used to fail:** with betas off HACS found no eligible release and
offered the default branch instead. It then looked for a release asset on a tag
called `main`, which does not exist — so the download 404'd and the wheel spun
forever. Nothing was wrong with your setup.

**Every v4 release before 4.0.0 was a pre-release**, because the whole v4 line
ran through alphas and betas. With betas off, HACS had nothing in the v4 line
to offer at all.

The rest of this section is kept because the mechanism will recur the next time
a long pre-release series runs — and because it explains why the problem
appeared when it did. (Found by @pk-1966, who worked it out unaided.)

**The mechanism, read from HACS's own source** rather than guessed (the source
of HACS before Home Assistant 2026.11; whether the Marketplace reads releases
the same way has not been checked):
`RepositoryBase.get_releases()` makes ONE call to GitHub's release list and
filters what comes back — it does not paginate. GitHub returns 30 releases per
page by default, so HACS only ever sees the 30 most recent.

By the end of the beta there were **49 v4 pre-releases** on top of v3.5.2. All
thirty HACS saw were pre-releases, every one skipped when betas were off, and
it ended up with an empty list — hence the fallback to the default branch.

This is also why it used to work: the stable release stayed reachable until the
**thirtieth** alpha pushed it out of the window. And it is why publishing 4.0.0
fixes it outright rather than merely improving it — there is a non-pre-release
inside the window again.

**Install v3.5.1 manually:** download the `ha_roomba_plus.zip` asset from the
[v3.5.1 release](https://github.com/johnnyh1975/ha_roomba_plus/releases), unpack
it into `config/custom_components/roomba_plus/`, and restart. HACS will then
show it as installed but will not offer updates for it.

That is a consequence of running a long alpha in the same repository as the
stable line, not something you did wrong.

### Mission progress gets stuck reporting a completed room as still in progress

**Applies to:** i/s series · handled since 2.8.0

On lewis-firmware robots (i7+/s9+), the robot occasionally reports a brief non-cleaning phase between rooms that can confuse the progress sensor. As of v2.8.0, Roomba+ detects these transitions automatically using your robot's real per-room cloud time estimates, so this should self-correct within the next room change. If it doesn't, call `roomba_plus.advance_room` to manually move to the next room — it's a no-op if the robot is actively cleaning or already at the last planned room, so it's safe to call speculatively.

### Rooms from another floor appear in the list

**Applies to:** Robots with several maps · fixed in 4.2.4 and 4.2.5

Fixed in v4.2.4 for rooms and v4.2.5 for zones. Before those, every map
the robot knew was merged into one flat list, so a room from a map the
robot was not on could be selected — and produced the 224 above.

### Mission sensors on Prime robots

**Applies to:** Prime robots · fixed in 4.0.0a41 and a42

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

### Lifetime energy went backwards, or utilisation read above 100%

**Applies to:** Classic robots · fixed in 4.0.0b4

Fixed in v4.0.0b4. Both sensors could violate their own contracts: the
lifetime energy figure is a `total_increasing` statistic, and one that
decreases corrupts long-run history in a way that only shows up months
later as a wrong-looking graph.

Energy now floors at a persisted high-water mark, so a source value that
drops no longer drags the total down with it. Utilisation is capped at
100%.

If your history already contains a dip, the statistic will pick up from
the high-water mark rather than repairing the recorded past — Home
Assistant's own statistics tools can adjust historical values if that
matters to you.

### Total energy consumed shows an unexpected value after upgrading to v2.5 on a Roomba 980

**Applies to:** 900-series · upgrading to 2.5

Expected — v2.5 corrects the energy calculation for 900-series robots. The 980/985 firmware reports a raw BMS value approximately 3.73× the actual mAh; previous versions used this raw value directly. After upgrading, the sensor shows the correct lower value and continues accumulating from that point.

### The coverage heatmap does not line up with the room map

**Applies to:** Classic robots · fixed in 4.0.0a45

Fixed in v4.0.0a45. It was drawn upside down against every other map —
measuring upward from the bottom of the grid while the rest measure
downward from the top. A row of cells at one edge was also always
missing, for a related reason.

If the two still disagree after updating, that is a different problem
and worth reporting: mark two or three known places on both images (the
dock is the easiest) and say where each one landed.

### The per-room sensors did not appear after enabling the option

**Applies to:** Prime robots · fixed in 4.0.0a47

Fixed in v4.0.0a47 for Prime robots, where the option created no
entities at all. If they are still missing after updating, a diagnostics
download will show whether the region names have arrived —
`region_names.merged` lists every room and zone the integration knows.

### Room names are missing on a local-only install

**Applies to:** Classic robots without an account · fixed in 4.0.0b1

Fixed in v4.0.0b1. Zone names you have saved live in the integration's
own options and need no cloud — but they were being discarded when no
cloud credentials were configured.

If you have never configured cloud credentials at all, there is nothing
stored to recover. Region ids can be discovered by sending a room clean
with a candidate id while the robot is docked: a valid id starts a
mission within about ten seconds, an invalid one does nothing at all.
Recall the robot and try the next.

**From the dock only, and re-dock between probes.** This is not a
convenience — off the dock, a region-targeted start does nothing
whatever the id, so a valid id and an invalid one look identical and
the probe reads the entire id space as invalid. Measured across eight
sends in four different states (@Young9898).

That launches real missions and advances the robot's mission and
evacuation counters, so it is a last resort rather than a setup step.

### Something I asked for just did not happen (before v4.1.0)

**Applies to:** Every robot · fixed in 4.1.0

Four separate faults shared one symptom: nothing happened, and no error
appeared anywhere. A rejected command and a robot that was already where
you wanted it look identical from Home Assistant.

All four are fixed in v4.1.0. If you saw any of these on an earlier
version, that is what it was:

**A schedule created from the calendar never appeared in the iRobot
app** — on i7, i8, s9, j7 or any other i/s/j robot. The calendar wrote
the wrong shape under the right key and the robot discarded it. Reading
always worked, so the calendar itself looked correct.

**A zone clean went to the wrong place, or nowhere.** `clean_zone` sent
zones as if they were rooms. Region ids are numbered per map, so the
wrong type usually names a real but different region — the robot did
something, just not what you asked.

**The clean-zone button cleaned the wrong room.** If your selection was
on a map the robot had not run on most recently, the button discarded it
and repeated whatever ran last.

**`clean_room` answered "unknown room" for a room that exists.** Only
rooms on the most recently used map could be named. Households that keep
a small secondary map — a bathroom-only map for spot cleaning, say —
lost access to every room on their main map after using it.

None of these needs any action beyond updating. If you built an
automation around one of them, though, it is worth re-checking: an
automation written to work around a bug can break when the bug does not.

### "Robot MQTT connection lost during mission" fires right after starting a mission

**Applies to:** Classic robots · fixed in 2.9.0

Fixed in v2.9.0. A genuine, benign Wi-Fi gap of a few minutes right after undocking (the robot reassociating with your router while it physically moves away) was previously misreported as a sustained connectivity problem — this affected any robot, but was more common on older robots with weaker Wi-Fi hardware or an aftermarket battery. The watchdog now waits at least 7 minutes after a mission starts before it can fire at all, regardless of silence duration; a genuine outage later in the mission is still caught normally. If you still see this fire within the first 7 minutes of a mission on v2.9.0 or later, that's unexpected — please open an issue with the last known phase and silence duration shown in the message.

</details>

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
