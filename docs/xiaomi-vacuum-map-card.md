[Roomba+](../README.md) › [Documentation](README.md) › xiaomi-vacuum-map-card

# Room cleaning from the xiaomi-vacuum-map-card

**Applies to:** Smart Map robots with the iRobot account · Prime

[lovelace-xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) (XVMC) by PiotrMachowski shows the robot's map on a dashboard. With Roomba+ you tap one or more rooms on it and the robot cleans them.

> **You need:** the iRobot account added to Roomba+ (Configure → iRobot cloud credentials), and the card installed from the Marketplace (HACS before Home Assistant 2026.11). The room outlines appear once the robot's positions have been lined up with the floor plan, usually after two to four missions that pass through doors.

---

## Set it up

**Applies to:** Smart Map robots with the iRobot account · Prime

<a id="path-a--xvmc-v241-recommended"></a>

The card knows Roomba+ from its version 2.4.1 on; the card's own page for it is [`johnnyh1975RoombaPlus.md`](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card/blob/master/docs/templates/johnnyh1975RoombaPlus.md).

1. Add the card with these basics:

   ```yaml
   type: custom:xiaomi-vacuum-map-card
   entity: vacuum.roomba
   vacuum_platform: roomba_plus
   map_source:
     camera: image.roomba_rooms_map   # your room map, see below
   calibration_source:
     camera: true                     # reads calibration_points from the map
   ```

2. In the card editor, select **Generate Room Configs**. The card reads the room outlines, names and icons from the map and fills in the rooms. Selecting the platform alone draws no rooms; this step does.

The card then offers three ways to clean the selected rooms:

| Mode | Action | Shown by default |
|---|---|---|
| Clean room | `roomba_plus.clean_room` | yes |
| Clean room (two pass) | `roomba_plus.clean_room` with `two_pass: true` | no, add it to `map_modes` |
| Clean room (smart start) | `roomba_plus.smart_start`, which checks your [blocking sensors](guides/start-conditions.md) first | yes |

If you see these modes after choosing Roomba+, the card is set up right. If Roomba+ is not in the `vacuum_platform` list, your card is older than 2.4.1: update it, or use [the manual setup](#path-b--xvmc-before-v241-manual).

## Which map to use

**Applies to:** Smart Map robots with the iRobot account · Prime

Use the **Room map**, `image.<robot>_rooms_map`. It is the only image that tells the card where the rooms are. An installation from an older version may call it `image.<robot>_rooms_cleaning_map`; it is the same entity. Find yours under Developer Tools → States by filtering for `image.`. The examples here use `image.roomba_rooms_map`.

A Prime robot has two more images. Neither works as the card's map:

| Entity | What it is for |
|---|---|
| `image.<robot>_prime_cleaning_map` | A picture card showing the clean in progress. It tells the card nothing about where the rooms are |
| `image.<robot>_map` | The raw live map, before drawing |

The Room map draws the clean in progress too, so you see the robot's progress on the card as well.

## What works and what doesn't

**Applies to:** Smart Map robots with the iRobot account · Prime

<a id="what-works--and-what-doesnt"></a>

The card also has zone and go-to-point modes. iRobot robots clean whole rooms only: they have no command to clean a drawn rectangle or drive to a point. Set up only the room modes.

| Card mode | `selection_type` | With Roomba+ |
|---|---|---|
| Room | `ROOM` | ✅ cleans the rooms |
| Zone rectangle | `MANUAL_RECTANGLE`, `PREDEFINED_RECTANGLE` | ❌ the robot has no such command |
| Pin and go | `MANUAL_POINT`, `PREDEFINED_POINT` | ❌ the robot has no such command |

- `roomba_plus.clean_sequence` is not a zone action. It starts one robot after another has finished.
- `vacuum.send_command` with `goto` does nothing.

Two things the room mode needs:

- **A Smart Map robot.** `clean_room` works on i, s and j robots, the Braava jet m6 and Prime robots. On a 900 series or another robot without Smart Maps it stops with an error: those robots clean the whole floor.
- **Room names the robot knows.** The name the card sends must be a room on the robot's map, as listed under Configure → **Rooms & zones**. A name only typed into the card is refused.

## Room names and ids

**Applies to:** Smart Map robots with the iRobot account · Prime

To list the rooms the map knows, paste this in Developer Tools → Template:

```yaml
{{ state_attr('image.roomba_rooms_map', 'rooms').keys() | list }}
```

Each room is listed by the name the room select shows, your own name for it first. Each room also carries `room_id`, a plain-letters version of the name (`Küche` → `kuche`), and `region_id`, the robot's number for it. Use `room_id` as the card's id. `clean_room` accepts the name, the `room_id` and the account's original name alike, upper or lower case, and keeps working after the robot relearns its map.

On a Prime robot, renaming a room in the iRobot app does not break a saved card: the card sends the room's id, not its name.

## Is the map lined up yet?

**Applies to:** Smart Map robots with the iRobot account · Prime

```yaml
{{ state_attr('image.roomba_rooms_map', 'alignment_pending') }}
```

`true` means Roomba+ has not yet lined up the robot's positions with the floor plan: the outlines may be turned or missing, and `calibration_points` is not there yet. It usually takes two to four missions that pass through doors. On i robots with firmware 22 or newer the positions come from the iRobot account, so no mission of your own is needed for it. How the map comes together: [maps](explanations/maps.md).

## Troubleshooting

**Applies to:** Smart Map robots with the iRobot account · Prime

**The card shows no rooms**
- Roomba+ must be the card's `vacuum_platform`, and you must have selected **Generate Room Configs**.
- Check that the map has calibration: `{{ state_attr('image.roomba_rooms_map', 'calibration_points') }}` should not be empty. If it is, see [Is the map lined up yet?](#is-the-map-lined-up-yet).
- Check that the iRobot account is added (Configure → iRobot cloud credentials).
- On Classic robots, the **Map learning** sensor shows how far the map has come; more missions through doors raise it.

**The outlines sit in the wrong place or are turned**
- The map is not lined up yet (`alignment_pending` is `true`). Wait for a few more missions.

**Tapping a room does nothing or shows an error**
- Run the action on its own to see the real message: Developer Tools → Actions → `roomba_plus.clean_room` with `room_name: <your room>`.
- *Unknown room*: the name is not a room on the robot's map. Check the spelling under Configure → Rooms & zones. Accents and spaces are forgiven (`kuche` finds `Küche`); a room that is not on the map is not.
- *Not supported*: the robot has no Smart Maps (see [What works and what doesn't](#what-works-and-what-doesnt)).

**A zone or pin-and-go mode does nothing**
- Expected: iRobot robots have neither. Keep only the room modes.

**Generate Room Configs makes ids the card rejects, such as `Küche`**
- Older card versions took the room's name as its id. Replace each `id:` with the room's `room_id` from the map's attributes.

**Generate Room Configs shows rooms of another robot**
- Each robot's Room map carries only that robot's rooms. Check that `entity` and `map_source` in the card point at the same robot.

**You can select only five rooms**
- Some card versions limit the Roomba+ modes to five rooms; Roomba+ has no such limit. Set up the rooms by hand ([below](#path-b--xvmc-before-v241-manual)) without `max_selections`.

## Manual setup

**Applies to:** Smart Map robots with the iRobot account · Prime

<a id="path-b--xvmc-before-v241-manual"></a>

For a card older than 2.4.1, or to get past the five-room limit, write the rooms into the card yourself. Every value is in the map's `rooms` attribute, so this is copying, not measuring.

<details>
<summary>The <code>rooms</code> attribute and a matching card configuration</summary>

Under Developer Tools → States, open `image.roomba_rooms_map`. Its `rooms` attribute looks like this (coordinates in millimetres from the dock, the same space as `calibration_points`):

```json
{
  "Dining Room": {
    "outline": [[200.5, 310.2], [460.1, 310.2], [460.1, 490.8], [200.5, 490.8]],
    "x": 330.3, "y": 400.5,
    "name": "Dining Room", "room_id": "dining_room",
    "icon": "mdi:silverware-fork-knife"
  }
}
```

Copy `outline`, `x`, `y` and `icon` of each room into the card:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_source:
  camera: image.roomba_rooms_map
calibration_source:
  camera: true
map_modes:
  - template: vacuum_clean_segment
    service_call_schema:
      service: roomba_plus.clean_room
      service_data:
        entity_id: "[[entity_id]]"
        room_name: "[[selection]]"
        ordered: true
    predefined_selections:
      - id: "dining_room"                # the room's room_id
        outline:
          - [200.5, 310.2]
          - [460.1, 310.2]
          - [460.1, 490.8]
          - [200.5, 490.8]
        label:
          text: "Dining Room"
          x: 330.3
          y: 400.5
        icon:
          name: mdi:silverware-fork-knife
          x: 330.3
          y: 400.5
```

Selecting several rooms sends them all as a list. To check your blocking sensors before starting, call `smart_start` instead:

```yaml
    service_call_schema:
      service: roomba_plus.smart_start
      service_data:
        entity_id: "[[entity_id]]"
        rooms: "[[selection]]"
```

</details>

## For dashboard authors

**Applies to:** Smart Map robots with the iRobot account · Prime

The Room map carries these attributes once the map is lined up. The card uses the first two; the others are for cards that draw their own layers.

| Attribute | What it holds |
|---|---|
| `calibration_points` | Three pairs of points matching robot millimetres to image pixels |
| `rooms` | Each room's outline, centre, name, `room_id`, `region_id` and icon |
| `zones` | Keep-out zones and obstacles the robot met, in robot millimetres |
| `door_markers` | Where the robot passed through doors |
| `furniture_candidates` | Spots where furniture probably moved |

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
