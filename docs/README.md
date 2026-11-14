[Roomba+](../README.md) › Documentation

# Roomba+ documentation

Everything about Roomba+ beyond the [start page](../README.md), by what you want to do. Every section says at the top which robots it applies to; the words it uses are explained in [Robots and generations](reference/robots.md#how-the-pages-say-which-robots).

## Get started

| | |
|---|---|
| [Getting started](getting-started.md) | Install, set up, first clean, name the rooms, first automation, maintenance |
| [Installation](../README.md#installation) | From the Marketplace (Home Assistant 2026.11 and later), HACS or by hand |
| [Moving to Roomba+](MIGRATING.md) | From the built-in Roomba integration or roomba_rest980, more robots, replacing a robot |

## Do something

| | |
|---|---|
| [Clean rooms and zones](guides/clean-rooms.md) | `clean_room`, `vacuum.clean_area`, zones, skipping a room, naming rooms |
| [Start only when the house is ready](guides/start-conditions.md) | Blocking sensors, sending a paused robot home, quiet hours |
| [Schedules, presence and demand cleaning](guides/schedules.md) | The schedule as a calendar, cleaning when everyone is out or the floor is dirty, rooms that are due |
| [Maintenance](guides/maintenance.md) | What is due, the to-do list, reminders, care guides, robot health |
| [Care guides](care/README.md) | How to clean or replace each part, in eight languages |
| [Maps and dashboards](guides/dashboards-and-maps.md) | The map images, their colours, the device tracker, map cards |
| [Notifications and blueprints](guides/notifications.md) | Blueprints, device triggers, event entities and events |
| [Scripts, Assist and the mission record](guides/scripts-and-assist.md) | Response actions, the mission's start and rooms, the REST API |
| [Automations](AUTOMATIONS.md) | Recipes, the shipped blueprints and a starter dashboard |
| [xiaomi-vacuum-map-card](xiaomi-vacuum-map-card.md) | The interactive map card with Roomba+ |

## Look something up

| | |
|---|---|
| [Features](FEATURES.md) | Everything Roomba+ does, by task, and where each old section went |
| [Robots and generations](reference/robots.md) | Classic and Prime, which generation has what, mopping robots |
| [Entities](reference/entities.md) | Every entity with its id, robots, and whether it starts disabled (generated) |
| [Actions](reference/actions.md) | Every `roomba_plus.*` action with its fields (generated) |
| [Options](reference/options.md) | Every step and option under Configure (generated) |
| [Status, controls and diagnostics](reference/status-and-controls.md) | What the status says, what the controls do, debug capture |
| [Events and device triggers](reference/events.md) | What fires when, with its fields |
| [REST API](API.md) | Endpoints for cards and scripts |

## Understand it

| | |
|---|---|
| [V4/Prime robots](PRIME.md) | What works on the cloud robots, what does not, and what is not yet confirmed |
| [How a 900-series finds its rooms](explanations/how-rooms-are-found.md) | Areas from coverage, and what they can and cannot give you |
| [How the maps are drawn](explanations/maps.md) | Frames, mirroring, rotation, map drift, layout changes |
| [Progress and time estimates](explanations/estimates.md) | Mission progress, the current room, the remaining time |
| [Mission history and statistics](explanations/history.md) | The mission log, unusual missions, stuck spots, performance |
| [The words on your screen come from iRobot](explanations/words-from-irobot.md) | Why texts and units read as in the iRobot apps |
| [What leaving an option off costs you](explanations/options.md) | The optional features and their cost |
| [Known limitations](explanations/limitations.md) | What does not work, and why |
| [Data privacy](DATA_PRIVACY.md) | Where your data goes, how often, and what a diagnostics download contains |
| [Comparison](COMPARISON.md) | Roomba+, the built-in integration and roomba_rest980 side by side |

## Fix something

| | |
|---|---|
| [Troubleshooting](TROUBLESHOOTING.md) | Problems by area, each with its cause and fix |
| [Upgrade notes](UPGRADING.md) | What changes when you update, and what to do |
| [Release notes](https://github.com/johnnyh1975/ha_roomba_plus/releases) | Everything that changed in each version |

## Contribute

| | |
|---|---|
| [Working on Roomba+](../AGENTS.md) | The rules and the checks to run before a pull request |
| [Architecture](ARCHITECTURE.md) | How the integration is put together |
| [Documentation demo](../scripts/docs_demo/README.md) | Regenerating the screenshots |

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
