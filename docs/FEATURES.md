[Roomba+](../README.md) › [Documentation](README.md) › Features

# Features

What Roomba+ does, by what you want to do. Each line leads to the page that explains it.

![The device page of a Roomba 980 in Home Assistant: device info, controls, the sensors and the activity log](images/device-page.png)

## Clean

- **One room, several in order, a zone or a Home Assistant area**, with settings per room; skip the room it is in; name your rooms → [Clean rooms and zones](guides/clean-rooms.md)
- **Only when the house is ready**: blocking sensors, a paused robot sent home, a Prime robot's quiet hours and start blocks → [Start only when the house is ready](guides/start-conditions.md)
- **On a schedule, when everyone is out, when the floor is dirtier than usual, the rooms that are due** → [Schedules, presence and demand cleaning](guides/schedules.md)

## Keep it running

- **Parts and care tasks**: what is due, the to-do list, reminders, care guides with drawings, iRobot's part counters → [Maintenance](guides/maintenance.md)
- **Robot health**: battery and dock contacts, navigation, reset causes → [Maintenance: robot health](guides/maintenance.md#robot-health)

## See and use

- **Maps**: the live cleaning path, the rooms, a floor plan per map, the coverage heatmap, where the robot is → [Maps and dashboards](guides/dashboards-and-maps.md)
- **Notifications**: shipped blueprints, device triggers, event entities, events → [Notifications and blueprints](guides/notifications.md)
- **Scripts and Assist**: ask for the rooms, the missions or a plan; react to a mission's start → [Scripts, Assist and the mission record](guides/scripts-and-assist.md)
- **Ready-made recipes and a dashboard** → [Automations](AUTOMATIONS.md)

## Look something up

- [Robots and generations](reference/robots.md): Classic and Prime, which generation has what, mopping robots
- [Status, controls and diagnostics](reference/status-and-controls.md)
- [Entities](reference/entities.md), [Actions](reference/actions.md), [Options](reference/options.md): generated from the code
- [Events and device triggers](reference/events.md)
- [REST API](API.md)

## Understand it

- [How a 900-series finds its rooms](explanations/how-rooms-are-found.md)
- [How the maps are drawn](explanations/maps.md)
- [Progress and time estimates](explanations/estimates.md)
- [Mission history and statistics](explanations/history.md)
- [The words on your screen come from iRobot](explanations/words-from-irobot.md)
- [What leaving an option off costs you](explanations/options.md)
- [Known limitations](explanations/limitations.md)
- [V4/Prime robots](PRIME.md), [Data privacy](DATA_PRIVACY.md), [How it compares](COMPARISON.md)

## Where did it go?

Up to 4.6.0b2 this page held everything in one. Each of its sections now lives on the page that fits it; a link to one of the old sections lands here, on its row.

| Section | Now in |
|---|---|
| <a id="tier-legend"></a>Tier legend | [robots](reference/robots.md#how-the-pages-say-which-robots) |
| <a id="which-generation-section-by-section-460"></a>Which generation, section by section | [robots](reference/robots.md#which-generation-has-what) |
| <a id="status-control--error-intelligence"></a>Status, control & error intelligence | [status-and-controls](reference/status-and-controls.md) |
| <a id="debug-capture-460"></a>Debug capture | [status-and-controls](reference/status-and-controls.md#debug-capture) |
| <a id="child-lock-eco-charge--gentle-mode-switches-v320-v343"></a>Child lock, eco charge & gentle mode switches | [status-and-controls](reference/status-and-controls.md#controls) |
| <a id="missions-for-scripts-and-assist-460"></a>Missions for scripts and Assist | [scripts-and-assist](guides/scripts-and-assist.md#ask-from-a-script-rooms-missions-plans) |
| <a id="mission-started-rooms-and-areas-460"></a>Mission started, rooms and areas | [scripts-and-assist](guides/scripts-and-assist.md#when-a-mission-starts-and-which-room-it-is-in) |
| <a id="when-each-area-was-last-vacuumed-and-mopped-460"></a>When each area was last vacuumed and mopped | [scripts-and-assist](guides/scripts-and-assist.md#when-each-area-was-last-vacuumed-and-mopped) |
| <a id="send-home-after-a-pause-460-opt-in"></a>Send home after a pause | [start-conditions](guides/start-conditions.md#send-home-after-a-pause) |
| <a id="cloud-stream-times--prime-460"></a>Cloud stream times — Prime | [status-and-controls](reference/status-and-controls.md#cloud-stream-times) |
| <a id="blueprints-460"></a>Blueprints | [notifications](guides/notifications.md#the-quickest-way-a-blueprint) |
| <a id="room-cleaning-zones--maps"></a>Room cleaning, zones & maps | [clean-rooms](guides/clean-rooms.md) |
| <a id="clean_room-vs-vacuumclean_area"></a>`clean_room` vs `vacuum.clean_area` | [clean-rooms](guides/clean-rooms.md#two-ways-to-clean-a-room) |
| <a id="skipping-the-current-room-command-437-button-444"></a>Skipping the current room | [clean-rooms](guides/clean-rooms.md#skipping-the-current-room) |
| <a id="cloud-zone-selector-attributes-smart-map-robots"></a>Cloud zone selector attributes | [clean-rooms](guides/clean-rooms.md#the-room-selects-attributes) |
| <a id="smart-start-with-blocking-sensor-gate"></a>Smart Start with blocking sensor gate | [start-conditions](guides/start-conditions.md#blocking-sensors-and-smart-start) |
| <a id="naming-rooms-and-areas-rebuilt-in-4219"></a>Naming rooms and areas | [clean-rooms](guides/clean-rooms.md#naming-rooms-and-areas) |
| <a id="zone-management"></a>Zone management | [clean-rooms](guides/clean-rooms.md#zone-management) |
| <a id="cloud-zone-sync--smart-map-robots"></a>Cloud zone sync | [clean-rooms](guides/clean-rooms.md#rooms-and-zones-from-the-irobot-account) |
| <a id="room-detection--900-series-v2100"></a>Room detection — 900-series | [how-rooms-are-found](explanations/how-rooms-are-found.md) |
| <a id="room-type-suggestion-v320-smart-tier"></a>Room type suggestion | [clean-rooms](guides/clean-rooms.md#naming-rooms-and-areas) |
| <a id="map-drift-detection-v310-ephemeral-tier"></a>Map drift detection | [maps](explanations/maps.md#map-drift) |
| <a id="layout-change-detection-v320"></a>Layout change detection | [maps](explanations/maps.md#layout-changes) |
| <a id="battery--dock-contact-monitoring-v2100"></a>Battery / dock contact monitoring | [maintenance](guides/maintenance.md#robot-health) |
| <a id="cleaning-map"></a>Cleaning map | [dashboards-and-maps](guides/dashboards-and-maps.md#the-cleaning-map) |
| <a id="rooms-map--smart-map-robots-v23"></a>Rooms map | [dashboards-and-maps](guides/dashboards-and-maps.md#the-rooms-map) |
| <a id="floor-plan-of-each-map--smart-map-robots-with-an-irobot-account-454"></a>Floor plan of each map | [dashboards-and-maps](guides/dashboards-and-maps.md#a-floor-plan-for-each-map) |
| <a id="where-the-robot-is-the-device-tracker"></a>Where the robot is: the device tracker | [dashboards-and-maps](guides/dashboards-and-maps.md#where-the-robot-is-the-device-tracker) |
| <a id="what-the-colours-on-the-map-mean"></a>What the colours on the map mean | [dashboards-and-maps](guides/dashboards-and-maps.md#what-the-colours-on-the-map-mean) |
| <a id="coverage-map-v22"></a>Coverage map | [dashboards-and-maps](guides/dashboards-and-maps.md#the-coverage-map) |
| <a id="room-names-in-japanese-chinese-or-korean"></a>Room names in Japanese, Chinese or Korean | [dashboards-and-maps](guides/dashboards-and-maps.md#room-names-in-japanese-chinese-or-korean) |
| <a id="room-cleaning-setup--ha-areas-vacuumclean_area-ha-20263"></a>Room cleaning setup — HA areas | [clean-rooms](guides/clean-rooms.md#clean-by-home-assistant-area) |
| <a id="one-time-setup"></a>One-time setup | [clean-rooms](guides/clean-rooms.md#one-time-setup) |
| <a id="using-the-action"></a>Using the action | [clean-rooms](guides/clean-rooms.md#using-the-action) |
| <a id="maintenance--health"></a>Maintenance & health | [maintenance](guides/maintenance.md) |
| <a id="maintenance-status-and-entries-450"></a>Maintenance status and entries | [maintenance](guides/maintenance.md#the-maintenance-entries) |
| <a id="configure--maintenance-450"></a>Configure → Maintenance | [maintenance](guides/maintenance.md#configure--maintenance) |
| <a id="care-guides-450"></a>Care guides | [maintenance](guides/maintenance.md#care-guides) |
| <a id="maintenance-repair-issues-450-opt-in"></a>Maintenance repair issues | [maintenance](guides/maintenance.md#repair-issues-opt-in) |
| <a id="maintenance-calendar-450-opt-in"></a>Maintenance calendar | [maintenance](guides/maintenance.md#maintenance-calendar-opt-in) |
| <a id="remaining-life-sensors"></a>Remaining life sensors | [maintenance](guides/maintenance.md#the-part-sensors) |
| <a id="cloud-synced-consumables"></a>Cloud-synced consumables | [maintenance](guides/maintenance.md#parts-counted-by-irobot) |
| <a id="status"></a>Status | [status-and-controls](reference/status-and-controls.md#status) |
| <a id="clean-base--dock-status"></a>Clean Base / dock status | [status-and-controls](reference/status-and-controls.md#status) |
| <a id="navigation-health-v310-smart-tier"></a>Navigation health | [maintenance](guides/maintenance.md#robot-health) |
| <a id="replacement-tracking"></a>Replacement tracking | [maintenance](guides/maintenance.md#care-reminders) |
| <a id="care-reminders-432-opt-in"></a>Care reminders | [maintenance](guides/maintenance.md#care-reminders) |
| <a id="maintenance-due-binary-sensor"></a>Maintenance due binary sensor | [maintenance](guides/maintenance.md#the-maintenance-entries) |
| <a id="maintenance-to-do-list-v340-rebuilt-in-450"></a>Maintenance to-do list | [maintenance](guides/maintenance.md#the-to-do-list) |
| <a id="dock-contact-health-v280"></a>Dock contact health | [maintenance](guides/maintenance.md#robot-health) |
| <a id="wear-intelligence"></a>Wear Intelligence | [maintenance](guides/maintenance.md#the-part-sensors) |
| <a id="device-diagnostics-opt-in"></a>Device diagnostics | [maintenance](guides/maintenance.md#robot-health) |
| <a id="health-score-trend-v320"></a>Health score trend | [maintenance](guides/maintenance.md#robot-health) |
| <a id="reset-diagnostics-v320"></a>Reset diagnostics | [maintenance](guides/maintenance.md#robot-health) |
| <a id="cleaning-cadence-health-v320"></a>Cleaning cadence health | [schedules](guides/schedules.md#rooms-that-are-due) |
| <a id="mission-history--room-intelligence"></a>Mission history & room intelligence | [history](explanations/history.md) |
| <a id="room-rhythms--mission-maps-v330"></a>Room rhythms & mission maps | [schedules](guides/schedules.md#rooms-that-are-due) |
| <a id="mission-log"></a>Mission log | [history](explanations/history.md#the-mission-log) |
| <a id="mission-progress-v26"></a>Mission progress | [estimates](explanations/estimates.md#mission-progress) |
| <a id="mission-phase-sensors"></a>Mission phase sensors | [status-and-controls](reference/status-and-controls.md#missions-and-errors) |
| <a id="room-intelligence--smart-map-robots"></a>Room intelligence | [history](explanations/history.md#rooms-in-the-history) |
| <a id="error-intelligence"></a>Error intelligence | [status-and-controls](reference/status-and-controls.md#missions-and-errors) |
| <a id="mission-anomaly-detection-v25"></a>Mission anomaly detection | [history](explanations/history.md#unusual-missions) |
| <a id="anomaly-explanation-v320"></a>Anomaly explanation | [history](explanations/history.md#unusual-missions) |
| <a id="stuck-pattern-time-correlation-v27"></a>Stuck pattern time-correlation | [history](explanations/history.md#where-the-robot-gets-stuck) |
| <a id="stuck-hotspot-clusters-v320"></a>Stuck hotspot clusters | [history](explanations/history.md#where-the-robot-gets-stuck) |
| <a id="performance-sensors"></a>Performance sensors | [history](explanations/history.md#performance-and-map-statistics) |
| <a id="map-intelligence--smart-map-robots-v26"></a>Map intelligence | [history](explanations/history.md#performance-and-map-statistics) |
| <a id="ha-long-term-statistics"></a>HA Long-Term Statistics | [scripts-and-assist](guides/scripts-and-assist.md#long-term-statistics) |
| <a id="rest-history-api"></a>REST history API | [scripts-and-assist](guides/scripts-and-assist.md#reading-the-history-from-outside-the-rest-api) |
| <a id="presence--scheduling"></a>Presence & scheduling | [schedules](guides/schedules.md) |
| <a id="cleaning-schedule-calendar-v340"></a>Cleaning schedule calendar | [schedules](guides/schedules.md#the-schedule-as-a-calendar) |
| <a id="presence-aware-scheduling--isjbraava-and-prime"></a>Presence-aware scheduling | [schedules](guides/schedules.md#presence-aware-scheduling) |
| <a id="demand-cleaning--smart-robots-with-cloud-v24"></a>Demand cleaning | [schedules](guides/schedules.md#demand-cleaning) |
| <a id="presence-analytics"></a>Presence analytics | [schedules](guides/schedules.md#presence-aware-scheduling) |
| <a id="mission-progress-is-an-estimate-not-coverage"></a>Mission progress is an estimate, not coverage | [estimates](explanations/estimates.md#progress-is-an-estimate-not-coverage) |
| <a id="favourites-are-per-robot"></a>Favourites are per robot | [clean-rooms](guides/clean-rooms.md#favourites-are-per-robot) |
| <a id="pad-washing-allowed-v4prime"></a>Pad washing allowed | [robots](reference/robots.md#pad-washing-and-drying) |
| <a id="pad-wash-interval-what-the-numbers-mean"></a>Pad wash interval: what the numbers mean | [robots](reference/robots.md#pad-wash-interval-what-the-numbers-mean) |
| <a id="pad-wetness-v4prime-a35"></a>Pad wetness | [robots](reference/robots.md#pad-wetness) |
| <a id="the-mop-tank-sensor-is-unreliable-on-some-combos"></a>The mop tank sensor is unreliable on some Combos | [robots](reference/robots.md#the-mop-tank-sensor-is-unreliable-on-some-combos) |
| <a id="per-room-preferences-v4prime"></a>Per-room preferences | [clean-rooms](guides/clean-rooms.md#per-room-preferences) |
| <a id="settings-showing-an-unusual-value"></a>Settings showing an unusual value | [status-and-controls](reference/status-and-controls.md#controls) |
| <a id="started-by-on-prime-a35"></a>Started by, on Prime | [status-and-controls](reference/status-and-controls.md#status) |
| <a id="the-third-phase-category-a35"></a>The third phase category | [status-and-controls](reference/status-and-controls.md#status) |
| <a id="do-not-disturb-v4prime-a34"></a>Do Not Disturb | [start-conditions](guides/start-conditions.md#quiet-hours-do-not-disturb) |
| <a id="start-blocked-v4prime-a34"></a>Start blocked | [start-conditions](guides/start-conditions.md#when-a-prime-robot-cannot-start) |
| <a id="prime-robots-what-classic-already-had-460"></a>Prime robots: what Classic already had | [PRIME](PRIME.md#what-prime-shares-with-classic) |
| <a id="connectivity-mop--configuration-reference"></a>Connectivity, mop & configuration reference | [options](reference/options.md) |
| <a id="braava--mop-sensors"></a>Braava / mop sensors | [robots](reference/robots.md#braava) |
| <a id="configuration-reference"></a>Configuration reference | [options](reference/options.md) |
| <a id="diagnostics-download"></a>Diagnostics download | [status-and-controls](reference/status-and-controls.md#diagnostics-download) |
| <a id="the-words-on-your-screen-come-from-irobot"></a>The words on your screen come from iRobot | [words-from-irobot](explanations/words-from-irobot.md) |
| <a id="every-option-and-what-it-costs-you-to-leave-it-off"></a>Every option, and what it costs you to leave it off | [options](explanations/options.md) |
| <a id="the-map"></a>The map (options) | [options](explanations/options.md#the-map) |
| <a id="entities-you-may-not-want"></a>Entities you may not want | [options](explanations/options.md#entities-that-take-space) |
| <a id="presence-and-demand-cleaning"></a>Presence and demand cleaning (options) | [options](explanations/options.md#presence-demand-cleaning-and-blocking-sensors) |
| <a id="blocking-sensors"></a>Blocking sensors (options) | [options](explanations/options.md#presence-demand-cleaning-and-blocking-sensors) |
| <a id="multi-robot-households"></a>Multi-robot households | [options](explanations/options.md#presence-demand-cleaning-and-blocking-sensors) |
| <a id="why-the-floor-is-getting-dirtier"></a>Why the floor is getting dirtier | [history](explanations/history.md#performance-and-map-statistics) |
| <a id="what-the-mission-record-says-went-wrong"></a>What the mission record says went wrong | [history](explanations/history.md#the-mission-log) |
| <a id="known-limitations"></a>Known limitations | [limitations](explanations/limitations.md) |
| <a id="planned-not-scheduled"></a>Planned, not scheduled | [limitations](explanations/limitations.md#planned-not-scheduled) |
| <a id="events--device-triggers"></a>Events & device triggers | [events](reference/events.md) |
| <a id="event-bus-v286"></a>Event bus | [events](reference/events.md#event-bus) |
| <a id="event-entities-4219"></a>Event entities | [events](reference/events.md#event-entities) |
| <a id="device-triggers"></a>Device triggers | [events](reference/events.md#device-triggers) |

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
