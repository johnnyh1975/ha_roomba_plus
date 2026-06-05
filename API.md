# Roomba+ REST API

> Integration version: **2.2.0** · Document version: 2026-06

The Roomba+ REST API provides programmatic access to mission history and
diagnostic data for use by the companion Lovelace card and third-party consumers.

---

## Authentication

All endpoints require a **Home Assistant Long-Lived Access Token**.

```
Authorization: Bearer <token>
```

**How to obtain a token:**

1. Open HA → Profile (bottom-left avatar)
2. Scroll to **Long-Lived Access Tokens**
3. Click **Create Token**, give it a name, copy the value

A 401 response (`{"message": "Unauthorized"}`) comes from the HA framework and
means the token is missing or invalid.

---

## Entry ID resolution

The `{entry_id}` path parameter is the HA config entry ID for the robot.

**Via JavaScript (card developers):**

```javascript
const result = await hass.callWS({
  type: 'config/entity_registry/get',
  entity_id: 'vacuum.your_roomba',
});
const entryId = result.config_entry_id;
```

**Via HA Developer Tools → Template:**

```
{{ config_entries.async_entries('roomba_plus')[0].entry_id }}
```

---

## Endpoints

### GET `/api/roomba_plus/{entry_id}/mission_history`

Returns mission history in one of two formats.

**Query parameters:**

| Parameter | Type    | Default | Range  | Description                          |
|-----------|---------|---------|--------|--------------------------------------|
| `format`  | string  | summary | —      | `summary`, `records`, or `hazards`   |
| `days`    | integer | 28      | 1–90   | Lookback window (summary format only)|

**Error responses:**

| Status | Condition                                          |
|--------|----------------------------------------------------|
| 400    | Unknown `format` value                             |
| 404    | Entry not found or belongs to different domain     |
| 503    | Integration still initialising after HA start      |
| 503    | Cloud coordinator unavailable (records format only)|

---

#### `format=summary` — DaySummary[]

Day-aggregated array, sorted ascending by date. Sourced from local MissionStore
(always available, no cloud credentials required).

```json
[
  {
    "date":      "2026-05-01",
    "total":     2,
    "completed": 2,
    "stuck":     0,
    "area_sqft": 340.0,
    "result":    "completed"
  }
]
```

| Field       | Type          | Nullable | Notes                                               |
|-------------|---------------|----------|-----------------------------------------------------|
| `date`      | ISO date      | No       | Local calendar date                                 |
| `total`     | integer       | No       | Total missions started                              |
| `completed` | integer       | No       | Missions classified as completed                    |
| `stuck`     | integer       | No       | Missions where robot got stuck                      |
| `area_sqft` | float         | Yes      | Sum for day; null for 600-series robots             |
| `result`    | string        | No       | Dominant: `error > stuck > completed > cancelled`   |

---

#### `format=records` — MissionRecord[]

Per-mission unified array, sorted ascending by start time. Prefers cloud
raw records (richer data) when cloud credentials are configured; falls back
to local MissionStore records.

Zone names from local MissionStore are injected into cloud records via
end-timestamp matching (±120 s tolerance) — F4a.

```json
[
  {
    "id":           "m_1700000000",
    "started_at":   "2026-05-01T09:00:00+00:00",
    "ended_at":     "2026-05-01T09:55:00+00:00",
    "duration_min": 55,
    "run_min":      48,
    "area_sqft":    320,
    "result":       "completed",
    "initiator":    "schedule",
    "zones":        ["Kitchen", "Living Room"],
    "error_code":   null,
    "recharges":    0,
    "evacuations":  1,
    "dirt_events":  14,
    "wifi_signal":  [72, 68, 65, 70],
    "source":       "cloud"
  }
]
```

| Field          | Type          | Nullable | Cloud only | Notes                                       |
|----------------|---------------|----------|------------|---------------------------------------------|
| `id`           | string        | No       | No         | `m_{unix_ts}` (local) or `c_{unix_ts}`      |
| `started_at`   | ISO datetime  | Yes      | No         | UTC                                         |
| `ended_at`     | ISO datetime  | Yes      | No         | UTC                                         |
| `duration_min` | integer       | No       | No         | Total incl. recharge                        |
| `run_min`      | integer       | Yes      | Yes        | Cleaning time excl. recharge                |
| `area_sqft`    | float         | Yes      | No         | Null for 600-series                         |
| `result`       | string        | No       | No         | See result values below                     |
| `initiator`    | string        | No       | No         | `schedule`, `localApp`, `rmtApp`, `none`    |
| `zones`        | string[]      | No       | No         | F4a: injected from local store              |
| `error_code`   | integer       | Yes      | No         | pauseId or local MQTT error code            |
| `recharges`    | integer       | Yes      | Yes        | Mid-mission recharge count                  |
| `evacuations`  | integer       | Yes      | Yes        | Clean Base evacuations                      |
| `dirt_events`  | integer       | Yes      | Yes        | Dirt detection events                       |
| `wifi_signal`  | integer[]     | Yes      | Yes        | Per-segment wlBars (0–100)                  |
| `source`       | string        | No       | No         | `cloud` or `local`                          |

**Result values:**

`completed`, `cancelled`, `cancelled_by_user`, `error`, `stuck`,
`stuck_and_resumed` (F6h — robot recovered and continued),
`stuck_and_abandoned` (F6h — mission ended after stuck event),
`blocked_timeout` (F6c — blocking sensors prevented start),
`unknown`

---

### `format=hazards` — HazardRecord[]

Returns obstacle pins from two sources: GridStore stuck hotspots (accumulated
from local MQTT pose data) and cloud-detected observed zone centroids (seeded
from the iRobot Smart Map UMF layer on first setup). Used by the companion card
to overlay hazard pins on the floor plan.

Available when `map_capability ≠ NONE` and GridStore data exists. Returns `[]`
for 600-series robots or when no data has been accumulated yet.

```json
[
  {
    "gx":          12,
    "gy":          8,
    "x_mm":        1800.0,
    "y_mm":        1200.0,
    "stuck_count": 5,
    "room_name":   null,
    "bearing_deg": 47,
    "distance_mm": 2160,
    "source":      "stuck_events"
  },
  {
    "gx":          null,
    "gy":          null,
    "x_mm":        2400.0,
    "y_mm":        -800.0,
    "stuck_count": null,
    "room_name":   null,
    "bearing_deg": 108,
    "distance_mm": 2530,
    "source":      "robot_learned"
  }
]
```

| Field          | Type          | Nullable | Notes                                                     |
|----------------|---------------|----------|-----------------------------------------------------------|
| `gx`           | integer       | Yes      | Grid cell x (null for `robot_learned` source)             |
| `gy`           | integer       | Yes      | Grid cell y (null for `robot_learned` source)             |
| `x_mm`         | float         | No       | Dock-relative mm — pose space for stuck_events            |
| `y_mm`         | float         | No       | Dock-relative mm                                          |
| `stuck_count`  | integer       | Yes      | Accumulated stuck events in cell (null for `robot_learned`)|
| `room_name`    | string        | Yes      | Always null in v2.2; populated by UmfAligner in v2.3      |
| `bearing_deg`  | integer       | No       | Compass bearing from dock (0 = up)                        |
| `distance_mm`  | integer       | No       | Euclidean distance from dock in mm                        |
| `source`       | string        | No       | `stuck_events` or `robot_learned`                         |

**`source` values:**
- `stuck_events` — GridStore stuck hotspot (≥ 3 events in cell cluster)
- `robot_learned` — UMF `observed_zones` centroid (cloud-detected obstacle)
- `keepout` — UMF `keepoutzones` centroid (v2.3+, user-configured no-go zone)

> **Coordinate space note (v2.2):** `x_mm`/`y_mm` for `stuck_events` are in
> pose space (dock at origin, mm). For `robot_learned` entries, coordinates are
> in UMF units (Q6 open — coordinate scale not yet confirmed). Card consumers
> should check `source` until v2.3 ships a resolved coordinate transform.


---

### GET `/api/roomba_plus/household`

Aggregates all Roomba+ robots in one request. Useful for multi-robot dashboards.

**Query parameters:**

| Parameter | Type    | Default | Range | Description         |
|-----------|---------|---------|-------|---------------------|
| `days`    | integer | 28      | 1–90  | Lookback window      |

```json
{
  "period_days": 28,
  "total": {
    "missions":       47,
    "completed":      43,
    "completion_pct": 91.5,
    "area_sqft":      8960.0
  },
  "robots": [
    {
      "entry_id":       "abc123",
      "name":           "Roomba Downstairs",
      "floor":          "Ground Floor",
      "missions":       31,
      "completed":      29,
      "completion_pct": 93.5,
      "area_sqft":      6200.0
    }
  ],
  "floors": [
    {
      "label":    "Ground Floor",
      "missions": 31,
      "completed":29,
      "area_sqft":6200.0
    }
  ]
}
```

`floors` is omitted when no robot has a floor label configured.
`area_sqft` is null when no robot has cloud records with area data.

**Floor label:** assign via Settings → Devices → Roomba+ → Configure → Settings → Floor label.

---

## Entity ID patterns

```
vacuum.{name}                     — primary vacuum control
image.{name}_cleaning_map         — live path map + keepout overlay (pose required, firmware < 3.20)
image.{name}_coverage_map         — EMA occupancy heatmap (v2.2+, pose required)
image.{name}_rooms_map            — room layout map for xiaomi-vacuum-map-card (v2.3+, SMART only)
sensor.{name}_*                   — all sensor entities
binary_sensor.{name}_*            — all binary sensor entities
select.{name}_zone_*              — zone selector(s)
```

Where `{name}` = vacuum entity name without domain prefix.
Example: `vacuum.roomba_downstairs` → `{name}` = `roomba_downstairs`

`image.{name}_rooms_map` exposes `calibration` and `rooms` attributes when
UmfAligner confidence ≥ 0.70. Use as the map source in xiaomi-vacuum-map-card
for room selection (no cleaning history overlay).

---

## Versioning

No explicit API versioning. New fields are **additive** — consumers must handle
unknown fields gracefully. Breaking changes increment the integration major version.

New response fields introduced in v2.1.0:
- `zones` now populated in cloud records (F4a)
- `result` gains `stuck_and_resumed`, `stuck_and_abandoned`, `blocked_timeout` values (F6c, F6h)

New in v2.2.0:
- `format=hazards` returns real data (GridStore stuck hotspots + `robot_learned` centroids)
- `GET /api/roomba_plus/household` endpoint added
- Local source `format=records` entries now populate `dirt_events`, `wifi_signal`, `evacuations` when cloud-enriched (previously always null)
- `wifi_signal` field semantics: 5-element histogram (bucket 0 = weakest signal, bucket 4 = strongest) — not a time-series

New in v2.3.0:
- `format=records` adds `room_coverage` (`dict[str, float] | null`) and `alignment_confidence` (`float | null`) fields
- `format=hazards` adds `source: "keepout"` entries for user-configured no-go zones; `room_name` now populated when UmfAligner confidence ≥ 0.70
- `image.{name}_rooms_map` entity added (SMART robots); exposes `calibration` and `rooms` attributes
- `image.{name}_cleaning_map` also gains `calibration` and `rooms` attributes (v2.3.0+)

---

## curl examples

```bash
# Get a long-lived token from HA (Profile → Long-Lived Access Tokens)
TOKEN="your_token_here"
HOST="homeassistant.local:8123"
ENTRY_ID="abc123def456"

# Day summary (last 28 days)
curl -H "Authorization: Bearer $TOKEN" \
  "http://$HOST/api/roomba_plus/$ENTRY_ID/mission_history"

# Per-mission records
curl -H "Authorization: Bearer $TOKEN" \
  "http://$HOST/api/roomba_plus/$ENTRY_ID/mission_history?format=records"

# Last 7 days summary
curl -H "Authorization: Bearer $TOKEN" \
  "http://$HOST/api/roomba_plus/$ENTRY_ID/mission_history?days=7"

# Obstacle hazards
curl -H "Authorization: Bearer $TOKEN" \
  "http://$HOST/api/roomba_plus/$ENTRY_ID/mission_history?format=hazards"

# Household aggregate (all robots)
curl -H "Authorization: Bearer $TOKEN" \
  "http://$HOST/api/roomba_plus/household?days=28"
```

---

## xiaomi-vacuum-map-card integration

From integration ≥ v2.3.0 (SMART robots, UmfAligner confidence ≥ 0.70):

- `image.{name}_rooms_map` — preferred map source; room polygons only, no
  cleaning history. Exposes `calibration` and `rooms` attributes.
- `image.{name}_cleaning_map` — live path map with keepout overlay. Also exposes
  `calibration` and `rooms` attributes from v2.3.0.

Both attributes are null until UmfAligner reaches confidence 0.70 (typically
after 3+ missions with door crossings registered in GeometryStore).

See the Roomba+ README for dashboard YAML examples.
