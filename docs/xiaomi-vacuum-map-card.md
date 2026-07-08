[← Roomba+](../README.md)

# xiaomi-vacuum-map-card integration

[lovelace-xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) (XVMC) by PiotrMachowski renders a live vacuum map with interactive room-cleaning support. This guide covers setting it up with Roomba+.

> **Requires:** Roomba+ v2.7.1+, cloud credentials configured, UmfAligner confidence ≥ 0.70 (typically after 2–4 missions with room crossings), xiaomi-vacuum-map-card installed via HACS.

---

## How it works

Roomba+ exposes two attributes on its map image entities that XVMC reads directly:

| Attribute | Read by | Description |
|---|---|---|
| `calibration_points` | `calibration_source: { camera: true }` | 3 anchor pairs mapping vacuum mm ↔ image pixels |
| `rooms` | `predefined_selections` (see below) | Dict of room polygons, names, MDI icons, and centroids |

**Additional vector-data attributes (v3.3.1), for dashboards drawing their own overlays** — not consumed by the native XVMC platform template above, but available for custom cards/templates:

| Attribute | Description |
|---|---|
| `zones` | Keep-out and robot-observed obstacle zones as raw polygons/points, pose-space mm |
| `door_markers` | Inferred door-crossing positions, pose-space mm |
| `furniture_candidates` | Cells flagged by the FURNITURE detector as likely-moved furniture, pose-space mm |

All three require the map entity to be in aligned mode (same gate as `rooms`/`calibration_points`) and are absent otherwise.

There are two ways to set up the room overlay, depending on your XVMC version:

- **XVMC v2.4.1 or newer (recommended):** the native Roomba+ platform is built
  in. Pick it as the `vacuum_platform` and let XVMC generate the room config for
  you — no manual coordinates. See **[Path A](#path-a--xvmc-v241-recommended)**.
- **XVMC older than v2.4.1:** the Roomba+ platform isn't available, so you define
  `predefined_selections` manually. The coordinates come straight from the
  `rooms` attribute — no measurement needed. See
  **[Path B](#path-b--xvmc-before-v241-manual)**.

To check your version: HACS → Frontend → Xiaomi Vacuum Map Card. If "Roomba+"
appears in the card editor's `vacuum_platform` dropdown, you're on v2.4.1+ and
can use Path A.

## What works — and what doesn't

XVMC is built for vacuums that expose **zone**, **go-to-point**, and **room**
cleaning. iRobot's protocol only supports **room (segment) cleaning**, so only
the room mode is usable here. This is an iRobot limitation, not a Roomba+ one —
no integration can add zone or pin-and-go to a robot whose firmware doesn't
offer it.

| XVMC mode | `selection_type` | Works with Roomba+? |
|---|---|---|
| Room / segment | `ROOM` | ✅ Yes — via `roomba_plus.clean_room` (smart-map robots) |
| Zone rectangle | `MANUAL_RECTANGLE` / `PREDEFINED_RECTANGLE` | ❌ No — iRobot has no coordinate-zone command |
| Pin & Go | `MANUAL_POINT` / `PREDEFINED_POINT` | ❌ No — iRobot has no go-to-point command |

So configure **only** a `ROOM` map mode. Don't add Zone or Pin & Go modes — they
have no valid service to call and will appear broken:

- A common mistake is wiring "Zone cleanup" to `roomba_plus.clean_sequence`. That
  is **not** a zone service — it's a multi-robot orchestration action ("start
  robot B when robot A finishes"). It has no concept of a rectangle.
- Likewise `vacuum.send_command` with `goto` does nothing — the local iRobot
  protocol exposed by this integration has no go-to-point command.

Two more requirements for the room mode to actually clean:

- **Smart-map robot only.** `clean_room` works on i7 / s9 / j-series and the
  Braava jet m6. On 900-series and other non-smart robots it raises a
  `ServiceValidationError` by design — those robots clean the whole floor.
- **Room names must match your Roomba+ Options.** The name passed to
  `clean_room` has to match a room saved on the robot's map (Roomba+ Options →
  Rooms & zones), not just a label you typed in the card YAML. An unknown name
  raises a `ServiceValidationError`.

---

## Path A — XVMC v2.4.1+ (recommended)

A native Roomba+ platform for xiaomi-vacuum-map-card was added in XVMC
v2.4.1 (June 2026) — confirmed present in the upstream repository's
source directly. It registers Roomba+ so XVMC can read this
integration's `rooms` attribute directly, and supplies the `clean_room`
/ `smart_start` service wiring.

1. Add the card and set the basics:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
vacuum_platform: johnnyh1975/ha_roomba_plus
map_source:
  camera: image.roomba_rooms_map   # your actual entity name; or rooms_cleaning_map
calibration_source:
  camera: true                     # reads calibration_points automatically
```

2. In the card editor, click **"Generate Room Configs"**. XVMC reads the `rooms`
   attribute and builds the `predefined_selections` (outlines, labels, icons)
   for you. That's it — no manual coordinate entry.

The merged template provides three room-cleaning modes:

- **Clean room** — `roomba_plus.clean_room` (`room_name`, `ordered`).
- **Clean room (two pass)** — `roomba_plus.clean_room` with `two_pass: true`.
- **Clean room (smart start)** — `roomba_plus.smart_start` (`rooms`), which
  applies blocking-sensor checks before starting.

"Clean room" and "Clean room (smart start)" are shown by default; "Clean room
(two pass)" is available but not in the default mode list — add it to your
`map_modes` if you want it shown without switching modes.

Seeing those three modes after selecting Roomba+ confirms the platform is
registered correctly.

---

## Path B — XVMC before v2.4.1 (manual)

On XVMC versions older than v2.4.1 the Roomba+ platform isn't in the dropdown
and the generate button can't read Roomba+ rooms, so you define
`predefined_selections` by hand. The good news: every value you need is already
in the `rooms` attribute, so this is copy-paste, not measurement.

First confirm your map entity name (below — this applies to both paths), then
follow Path B steps 1 and 2.

## Finding your map entity name

*(Needed for both Path A and Path B — this is the entity name you put in
`map_source`.)*

Roomba+ creates a `Rooms Map` image entity. Depending on when the integration was first installed, this entity may appear under one of two names:

| Installed | Entity ID |
|---|---|
| v2.7.1+ (fresh install) | `image.{prefix}_rooms_map` |
| Upgraded from earlier version | `image.{prefix}_rooms_cleaning_map` |

Both are the same entity with the same attributes. Check **Developer Tools → States** and filter for `image.` to find yours. The examples below use `image.roomba_rooms_map` — substitute your actual entity name.

---

## Path B, step 1 — Getting room coordinates

Open **Developer Tools → States**, find `image.roomba_rooms_map` (or `rooms_cleaning_map`), and look at the **Attributes** section. The `rooms` attribute contains everything you need:

```json
{
  "Dining Room": {
    "outline": [[200.5, 310.2], [460.1, 310.2], [460.1, 490.8], [200.5, 490.8]],
    "x": 330.3,
    "y": 400.5,
    "name": "Dining Room",
    "icon": "mdi:silverware-fork-knife"
  },
  "Kitchen": {
    "outline": [[460.1, 310.2], [700.0, 310.2], [700.0, 490.8], [460.1, 490.8]],
    "x": 580.0,
    "y": 400.5,
    "name": "Kitchen",
    "icon": "mdi:fridge"
  }
}
```

All coordinates are in **vacuum mm** (dock-relative, same space as `calibration_points.vacuum`). Copy `outline` and the centroid `x`/`y` for each room into `predefined_selections`.

---

## Path B, step 2 — Manual config

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_source:
  camera: image.roomba_rooms_map          # use your actual entity name
calibration_source:
  camera: true                            # reads calibration_points automatically
map_modes:
  - template: vacuum_clean_segment
    service_call_schema:
      service: roomba_plus.clean_room
      service_data:
        entity_id: "[[entity_id]]"
        room_name: "[[selection]]"
        ordered: true
    predefined_selections:
      - id: "Dining Room"
        outline:
          - [200.5, 310.2]               # from rooms.Dining Room.outline
          - [460.1, 310.2]
          - [460.1, 490.8]
          - [200.5, 490.8]
        label:
          text: "Dining Room"
          x: 330.3                        # from rooms.Dining Room.x
          y: 400.5                        # from rooms.Dining Room.y
        icon:
          name: mdi:silverware-fork-knife # from rooms.Dining Room.icon
          x: 330.3
          y: 400.5
      - id: "Kitchen"
        outline:
          - [460.1, 310.2]
          - [700.0, 310.2]
          - [700.0, 490.8]
          - [460.1, 490.8]
        label:
          text: "Kitchen"
          x: 580.0
          y: 400.5
        icon:
          name: mdi:fridge
          x: 580.0
          y: 400.5
```

`predefined_selections.id` must exactly match the room name in Roomba+ (same string passed to `clean_room`). Multi-room selection works automatically — `[[selection]]` passes a list when multiple rooms are tapped.

---

## Using `smart_start` with blocking sensors

If you use Roomba+'s blocking sensor feature, substitute `smart_start` so door/occupancy checks are applied before cleaning starts:

```yaml
service_call_schema:
  service: roomba_plus.smart_start
  service_data:
    entity_id: "[[entity_id]]"
    rooms: "[[selection]]"
```

---

## Finding room names

Room names come from Roomba+ Options → **Rooms & Zones**. To list all configured names:

```yaml
# Developer Tools → Template:
{{ state_attr('image.roomba_rooms_map', 'rooms').keys() | list }}
```

Names are case-insensitive in `clean_room` and survive map retraining (unlike `{pmap_id}_{region_id}` segment IDs).

---

## Alignment status

The `rooms` attribute and calibration are only accurate once UmfAligner confidence ≥ 0.70. Check:

```yaml
{{ state_attr('image.roomba_rooms_map', 'alignment_pending') }}
```

`true` means alignment is still in progress — room polygons shown use the fallback UMF coordinate space and may be rotated relative to robot orientation. Once 2–4 missions complete with room crossings, full alignment activates. On lewis firmware robots (i7+/i8+ firmware 22.x+), cloud traversal data bootstraps alignment automatically — no local pose data required.

---

## Troubleshooting

**No room overlay at all, and you're on XVMC v2.4.1+ (Path A):**
- Confirm **Roomba+** is selected as `vacuum_platform`. If it's not in the
  dropdown, you're on an XVMC older than v2.4.1 — update XVMC via HACS, or use
  [Path B](#path-b--xvmc-before-v241-manual) instead.
- After selecting Roomba+, you should see three room modes ("Clean room",
  "Clean room (two pass)", "Clean room (smart start)"). If you don't, the
  platform isn't registered — recheck the version.
- Run the **"Generate Room Configs"** button — selecting the platform alone does
  not draw the overlay; the generate step is what reads the `rooms` attribute
  and builds it.

**Outlines appear but at wrong positions / wrong scale:**
- Confirm you are on Roomba+ v2.7.1+. Prior versions emitted `rooms.outline` in image pixel space instead of vacuum mm, causing XVMC to misplace overlays at ~36% of the correct size.
- Check `alignment_pending`. If `true`, outlines use the fallback UMF space which may be rotated. Wait for full alignment.

**Card shows no rooms / blank map:**
- Check `calibration_points` is not null: `{{ state_attr('image.roomba_rooms_map', 'calibration_points') }}`
- Verify cloud credentials are configured (Settings → Configure → iRobot cloud credentials).
- Check `sensor.roomba_map_learning` — if below 80%, more cleaning missions with room crossings are needed.

**`outline` is not in `rooms` attribute:**
- Alignment may not have completed yet. The `rooms` attribute only populates after at least one successful render. Trigger a map refresh by starting and stopping a brief cleaning mission.

**A Zone or Pin & Go mode does nothing:**
- Expected — iRobot supports neither. See [What works — and what doesn't](#what-works--and-what-doesnt). Remove those modes and keep only a `ROOM` mode. In particular, `roomba_plus.clean_sequence` is a multi-robot orchestration action, not a zone service, and `vacuum.send_command` with `goto` has no effect.

**Tapping a room throws an error / nothing cleans:**
- Run the underlying action directly to see the real error: Developer Tools → Actions → `roomba_plus.clean_room` with `room_name: <your room>`.
- `ServiceValidationError: unknown room` → the name doesn't match a room saved on the robot's map. Check the exact spelling in Roomba+ Options → Rooms & zones; the name in the card must match (accent/slug variants like `kuche`→`Küche` are handled, but a name that isn't saved can't resolve).
- `ServiceValidationError` about capability → the robot isn't a smart-map model. `clean_room` needs i7 / s9 / j-series or the Braava jet m6; other robots clean the whole floor instead.

**Room names don't match card options:**
- Names come from Roomba+ Options → Rooms & zones. Update labels there — changes take effect on next cloud refresh.

**`rooms_cleaning_map` vs `rooms_map`:**
- Both names refer to the same entity. If you have `rooms_cleaning_map` (older install), use that name in your config. It has identical attributes. You can rename it manually via Settings → Entities if preferred.

**"Generate Room Configs" produces IDs XVMC rejects (e.g. non-ASCII names like "Küche"):**
- Root cause confirmed and fixed upstream (PR pending against `PiotrMachowski/lovelace-xiaomi-vacuum-map-card`): the card's generic room-config generator used the `rooms` attribute's object key (a display name) as the id, ignoring the `room_id` field this integration already provides specifically for this — an ASCII-safe slug (`"Küche"` → `"kuche"`, `"Test ü"` → `"test_u"`). Until the fix is merged, manually replace the `id:` values with the matching `room_id` from the attribute (Developer Tools → States → your `rooms_map` entity).

**"Generate Room Configs" includes rooms from a different robot:**
- Not a data issue on this integration's side — each robot's `rooms_map` entity attribute is scoped strictly to that robot's own config entry (verified against the source; no shared/global state involved). The card's generic room-config generator is also confirmed correctly scoped to the configured `map_source` entity only — it does not aggregate across entities. The remaining suspect is the `roomba_plus`-specific platform template itself (not yet in the upstream card as of this writing); if you hit this, double-check your card config's `map_source`/`entity` first.

**Room selection stops working after 5 rooms are selected:**
- Root cause confirmed: the `roomba_plus` platform template hard-coded `max_selections: 5` on all three `ROOM` modes. This integration's own `clean_room`/`smart_start` actions have no such limit — the value has been removed from the template ahead of its upstream submission. Until that PR is merged, you can work around this by defining `map_modes`/`predefined_selections` manually ([Path B](#path-b--xvmc-before-v241-manual)) with no `max_selections` set.

---

## XVMC platform template (reference)

The native `johnnyh1975/ha_roomba_plus` platform template is what
[Path A](#path-a--xvmc-v241-recommended) uses — confirmed present in the
upstream repository's source directly. It defines three
`selection_type: ROOM` modes — `vacuum_clean_segment` (→ `clean_room`),
`vacuum_clean_segment_two_pass` (→ `clean_room` with `two_pass`), and
`vacuum_clean_segment_with_blocking` (→ `smart_start`). It contains no
coordinates itself: the geometry is read from this integration's `rooms`
attribute at runtime via the card's shared "Generate Room Configs"
mechanism, not from anything platform-specific.

**Three known issues, fixes submitted upstream, not yet merged as of
this writing** — see [Troubleshooting](#troubleshooting) above for each
one's workaround in the meantime:
- Generated `predefined_selections` IDs use the raw room name instead of
  the ASCII-safe `room_id` this integration provides
- With more than one card instance mounted at once, "Generate Room
  Configs" can pick up a different vacuum's rooms (a shared-code event-
  scoping bug, not specific to this platform)
- `max_selections: 5` is hard-coded in the platform template itself,
  not a real limit on this integration's side

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [Automations](AUTOMATIONS.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
