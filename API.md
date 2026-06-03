# Roomba+ REST API

> Integration version: **2.1.0** · Document version: 2025-05

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
| `format`  | string  | summary | —      | `summary` or `records`               |
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

### `format=hazards` — HazardRecord[] *(requires integration ≥ v2.2)*

Returns stuck-event hotspots from the occupancy grid with dock-relative
coordinates. Used by the companion card to overlay hazard pins on the floor plan.

Available in v2.2.0. Returns 400 in v2.1.x (unknown format value).

---

## Entity ID patterns

```
vacuum.{name}                     — primary vacuum control
image.{name}_cleaning_map         — live path map (pose data required, firmware < 3.20)
image.{name}_coverage_map         — coverage heatmap (≥ v2.2, pose required)
sensor.{name}_*                   — all sensor entities
binary_sensor.{name}_*            — all binary sensor entities
select.{name}_zone_*              — zone selector(s)
```

Where `{name}` = vacuum entity name without domain prefix.
Example: `vacuum.roomba_downstairs` → `{name}` = `roomba_downstairs`

---

## Versioning

No explicit API versioning. New fields are **additive** — consumers must handle
unknown fields gracefully. Breaking changes increment the integration major version.

New response fields introduced in v2.1.0:
- `zones` now populated in cloud records (F4a)
- `result` gains `stuck_and_resumed`, `stuck_and_abandoned`, `blocked_timeout` values (F6c, F6h)

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
```

---

## xiaomi-vacuum-map-card integration

`image.{name}_cleaning_map` exposes `calibration` and `rooms` attributes from
integration ≥ v2.2.0. See the xiaomi-vacuum-map-card documentation and the
Roomba+ README for dashboard YAML examples.
