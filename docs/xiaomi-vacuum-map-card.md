[← Roomba+](../README.md)

# xiaomi-vacuum-map-card integration

[lovelace-xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) by PiotrMachowski is a popular Lovelace card that renders a live vacuum map with interactive room-cleaning support. This guide covers setting it up with Roomba+.

> **Requires:** Roomba+ v2.7.0+, cloud credentials configured, UmfAligner confidence ≥ 0.70 (typically after 2–4 missions), xiaomi-vacuum-map-card installed via HACS.

---

## How it works

From v2.7.0, Roomba+ exposes two attributes on both map image entities that the card reads natively:

| Attribute | Key | Description |
|---|---|---|
| `calibration_points` | Read by `calibration_source: { camera: true }` | 3 anchor pairs mapping vacuum mm ↔ image pixels |
| `rooms` | Auto-read from `map_source.camera` | Dict of room polygons, names, and MDI icons |

No manual coordinate extraction or calibration is required. The card auto-detects both attributes.

---

## Minimal config

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_source:
  camera: image.roomba_cleaning_map   # or image.roomba_rooms_map
calibration_source:
  camera: true                         # reads calibration_points automatically
map_modes:
  - template: vacuum_clean_segment
    service_call_schema:
      service: roomba_plus.clean_room
      service_data:
        entity_id: "[[entity_id]]"
        room_name: "[[selection]]"
        ordered: true
```

The `rooms` attribute is read automatically from `map_source.camera`. Room names in the card match the names assigned in the Roomba+ Options → Rooms & Zones menu.

---

## Choosing the map source

| Entity | Best for | Notes |
|---|---|---|
| `image.roomba_cleaning_map` | Live cleaning view | Shows robot trajectory + room outlines when aligned |
| `image.roomba_rooms_map` | Static room layout | Dark background, cleaner for room selection |

Both expose the same `calibration_points` and `rooms` attributes. Either works as the card's `map_source.camera`.

---

## Room cleaning

### Using `clean_room` (recommended)

Room names in the card's `predefined_selections` are the display names from your Roomba+ zone configuration. They are case-insensitive and survive map retraining (names are stable; segment IDs are not).

```yaml
map_modes:
  - template: vacuum_clean_segment
    service_call_schema:
      service: roomba_plus.clean_room
      service_data:
        entity_id: "[[entity_id]]"
        room_name: "[[selection]]"
        ordered: true
```

Multi-room selection works automatically — `[[selection]]` passes a list when multiple rooms are selected.

### Using `smart_start` with blocking sensors

If you use Roomba+'s blocking sensor feature, use `smart_start` instead so door / occupancy checks are applied:

```yaml
service_call_schema:
  service: roomba_plus.smart_start
  service_data:
    entity_id: "[[entity_id]]"
    rooms: "[[selection]]"
```

---

## Finding room names

Room names come from Roomba+ Options → **Rooms & Zones** (the same names shown in the smart zone selector). To see all available names:

```yaml
# In Developer Tools → Template:
{{ state_attr('sensor.roomba_mission_progress', 'room_sequence') }}
# Or from the zone select entity options:
{{ state_attr('select.roomba_select_zone_cloud', 'options') }}
```

Alternatively, check `image.roomba_rooms_map` → Attributes → `rooms` in Developer Tools.

---

## Alignment status

The card's room outlines and calibration are only accurate once UmfAligner confidence ≥ 0.70. Check:

```
{{ state_attr('image.roomba_cleaning_map', 'calibration_points') }}
```

If this is `None`, alignment is still pending. Once ≥ 2–4 missions complete with room crossings (traversal events), alignment activates automatically. On lewis firmware robots (i7+/i8+ with firmware 22.x), bootstrap alignment from cloud traversal data now happens automatically — no local pose data required.

---

## Troubleshooting

**Card shows no rooms / blank map:**
- Check that `image.roomba_cleaning_map.attributes.calibration_points` is not `null`
- Check `sensor.roomba_map_learning` — if < 80 %, more cleaning missions are needed
- Verify cloud credentials are configured (Settings → Configure → iRobot cloud credentials)

**Room outlines are in wrong positions:**
- Alignment confidence may be low. Open `image.roomba_rooms_map` → Attributes → `alignment_pending`. If `True`, wait for more missions.
- After a map retrain, alignment re-runs automatically on the next cloud refresh.

**Room names don't match the card options:**
- Room names in the card come from your Roomba+ zone labels. Update them via Settings → Configure → Rooms & zones.

---

## XVMC platform template

A native `roomba_plus` platform template PR has been submitted to the xiaomi-vacuum-map-card repository ([#PR link]). Once merged, you can select **Roomba+** from the card's `vacuum_platform` dropdown and the service call schema is pre-filled automatically.
