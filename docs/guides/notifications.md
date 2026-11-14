[Roomba+](../../README.md) › [Documentation](../README.md) › Notifications and blueprints

# Notifications and blueprints

Get told when a mission ends, the robot is stuck or a part is due: with a shipped blueprint, a device trigger or an event. Every event and its fields: [Events](../reference/events.md). Ready-made recipes: [Automations](../AUTOMATIONS.md).

**Applies to:** all robots. A few triggers exist only where their entity does; the [device triggers](../reference/events.md#device-triggers) say which.

## The quickest way: a blueprint

**Applies to:** all robots; *Vacuum then Mop* needs a vacuum and a mop robot, *Demand Clean Alert* demand cleaning

*New in 4.6:* the blueprints come with the integration. Open **Settings → Automations & scenes → Blueprints**, pick one and fill in the robot and your notification action.

| Blueprint | What it does |
|---|---|
| **Roomba+ Curated Notifications** | Up to five notifications you pick: mission complete, maintenance due, robot stuck, map retrain detected, battery capacity critical ([recipe](../AUTOMATIONS.md#notification-blueprint)) |
| **Roomba+ Exception Notifications** | Nothing after a normal mission; a notification only when something is wrong: an error or stuck end, the same error again and again, stuck in the same place at the same time, unusual missions, scheduled cleans skipped |
| **Roomba+ Maintenance** | A part or care task is due, with the care guide one tap away and buttons on the phone to mark it done or snooze it |
| **Roomba+ Robot Went Silent** | The robot stopped reporting: every entity keeps its last value, so nothing else looks wrong |
| **Roomba+ Demand Clean Alert** | The robot started on its own because the floor was dirtier than usual |
| **Roomba+ Vacuum then Mop** | Starts a Braava when a Roomba finishes |
| **Roomba+ Smart Start on Away** | Starts cleaning when everyone leaves, at once or timed to be done before you are back |

The blueprints are copied to `blueprints/automation/roomba_plus/` when Home Assistant starts. A file that exists is never overwritten, a blueprint copied once is not copied again (so one you delete stays deleted), and one you imported through its link before is not copied beside it. A newer version of a shipped blueprint does not replace your copy; the release notes say when one changed, and importing it from its link again updates it.

## Without YAML: a device trigger

**Applies to:** all robots; the map retrain triggers on Classic Smart Map robots

In the automation editor, **Add trigger → Device**, pick the robot and one of its triggers: cleaning started, cleaning finished, stuck, bin full, start blocked, docked, error, room completed, maintenance due, health score drop, map retrain started or completed, firmware updated. Only the triggers that can fire on your robot are offered.

## On a dashboard, for every user: event entities

**Applies to:** all robots

`event.<robot>_mission_completed`, `_mission_started`, `_room_completed`, `_stuck`, `_mission_result`, `_maintenance` and `_command` change state when their moment happens, with the details as attributes. Unlike the bus events, which only administrators can subscribe to from the frontend, an entity reaches every user who can see the robot, and works as a *State* trigger in an automation.

## With all the details: an event

**Applies to:** all robots

Every moment is also an event on the bus (`roomba_plus_mission_completed`, `roomba_plus_stuck`, …) with everything Roomba+ knows about it: the rooms, the area, the result, why a mission was unusual. Use an *Event* trigger:

```yaml
triggers:
  - trigger: event
    event_type: roomba_plus_mission_completed
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ trigger.event.data.name }} finished ({{ trigger.event.data.result }}),
        {{ trigger.event.data.rooms_cleaned }} rooms
```

`entry_id` and `name` say which robot. The fields of every event: [Events](../reference/events.md#event-bus).

## A command that did not arrive

**Applies to:** all robots

A start that the robot never begins is the failure nobody notices. Roomba+ watches every start it sends; if no mission begins within 20 seconds (Classic) or 60 seconds (Prime), it fires `roomba_plus_command_not_delivered`, sends the command once more if the robot is idle, and `event.<robot>_command` changes state. The recipe: [Tell me when a start did not arrive](../AUTOMATIONS.md#tell-me-when-a-start-did-not-arrive-460).

---

[Documentation home](../README.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
