[← Roomba+](../README.md)

# Roomba+ REST API

Internal mission history, household aggregates, and obstacle data exposed as HA REST endpoints.

---

## Authentication

All endpoints require a Home Assistant long-lived access token passed as a Bearer header:

```bash
curl -H "Authorization: Bearer <long-lived-token>" \
     https://<ha-host>/api/roomba_plus/<entry_id>/mission_history
```

Unauthorized requests receive a `401` from the HA framework (`{"message": "Unauthorized"}`).

**Finding your `entry_id`:** Settings → Devices → Roomba+ → ⋮ → System information, or via Developer Tools:

```
{{ config_entries.async_entries('roomba_plus')[0].entry_id }}
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/roomba_plus/{entry_id}/mission_history` | Mission history (summary / records / hazards / export) |
| `POST` | `/api/roomba_plus/{entry_id}/mission_history/import` | Import a history export bundle |
| `GET` | `/api/roomba_plus/household` | Household aggregate across all robots |

### HTTP status codes

| Code | Condition |
|---|---|
| `200` | Normal response — may be empty array |
| `400` | Unknown `format` value |
| `404` | `entry_id` not found |
| `503` | Integration still initialising or cloud coordinator unavailable |

---

## GET /mission_history

```
GET /api/roomba_plus/{entry_id}/mission_history
```

| Parameter | Type | Default | Values | Notes |
|---|---|---|---|---|
| `format` | string | `summary` | `summary` / `records` / `hazards` / `export` | Response shape |
| `days` | int | 28 (summary) / 90 (records) | 1–90 | Ignored for `hazards` and `export` |

---

### format=summary

Day-aggregated view. Always available — uses local MissionStore, no cloud required.

**Ordering:** ascending by date (oldest first).

```json
[
  {
    "date": "2026-06-01",
    "total": 3,
    "completed": 2,
    "stuck": 1,
    "area_sqft": 640.5,
    "result": "stuck",
    "dirt_density": 0.42,
    "relative_to_baseline": 1.23,
    "room_dirt_index": {
      "19": 1.4,
      "21": 0.8
    }
  }
]
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `date` | string (YYYY-MM-DD) | Never | Local calendar date (HA timezone) |
| `total` | int | Never | All missions in this day |
| `completed` | int | Never | Missions with result `completed` or `stuck_and_resumed` |
| `stuck` | int | Never | Missions with result `stuck` or `stuck_and_abandoned` |
| `area_sqft` | float | Yes | Null for 600-series or days with no area data |
| `result` | string | Never | Dominant result: `error` > `stuck` > `completed` > `cancelled` > `none` |
| `dirt_density` | float | Yes | Median dirt events/m² for the day. Null without cloud credentials or < 5 records. |
| `relative_to_baseline` | float | Yes | `dirt_density / p30d_median`. 1.0 = average day; > 1.8 triggers `schedule_suboptimal` Repair Issue. |
| `room_dirt_index` | object | Yes | Per-room dirtiness relative to household average (1.0 = average). Keys are region IDs. Null until ≥2 rooms indexed. |

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=summary&days=14"
```

---

### format=records

Per-mission unified records. Source is cloud when credentials are configured and coordinator is healthy; falls back to local MissionStore.

**Ordering:** ascending by `started_at` (oldest first).

```json
[
  {
    "id": "m_1734567890",
    "started_at": "2026-06-01T14:22:00+00:00",
    "ended_at": "2026-06-01T15:04:00+00:00",
    "duration_min": 42,
    "run_min": 38,
    "area_sqft": 312.0,
    "result": "completed",
    "initiator": "schedule",
    "zones": ["Kitchen", "Hallway"],
    "error_code": null,
    "recharges": 0,
    "evacuations": 1,
    "dirt_events": 14,
    "wifi_signal": [0, 35, 65, 0, 0],
    "room_coverage": {"kitchen": 0.87, "hallway": 0.64},
    "alignment_confidence": 0.82,
    "source": "cloud"
  }
]
```

| Field | Type | Cloud source | Local source |
|---|---|---|---|
| `id` | string | `"c_{startTime}"` | `"m_{started_at_ts}"` |
| `started_at` | string (ISO UTC) | From `startTime` (unix) | From `started_at` |
| `duration_min` | int | `durationM` or `doneM` | `duration_min` |
| `run_min` | int / null | `runM` | Always null |
| `area_sqft` | float / null | `sqft` | `area_sqft` |
| `result` | string | `classified_result` | `result` |
| `initiator` | string | `initiator` | `initiator` |
| `zones` | array | Injected from local MissionStore | `zones` |
| `error_code` | int / null | Computed from `pauseId` | `error_code` |
| `recharges` | int / null | `chrgs` | Always null |
| `evacuations` | int / null | `evacs` | Always null |
| `dirt_events` | int / null | `dirt` | null (populated from merge if credentials configured) |
| `wifi_signal` | array / null | `wlBars` — 5-element histogram, index 0=weakest | null (populated from merge if credentials configured) |
| `room_coverage` | object / null | Per-room fraction from timeline | null |
| `alignment_confidence` | float / null | UmfAligner confidence 0.0–1.0 | null |
| `source` | string | `"cloud"` | `"local"` |

**`result` values:** `completed` · `cancelled` · `error` · `stuck` · `stuck_and_resumed` · `stuck_and_abandoned` · `blocked_timeout` · `cancelled_by_user` · `unknown`

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=records&days=30"
```

---

### format=hazards

Obstacle pin array from GridStore stuck hotspots and UMF-detected obstacle centroids.

**Gate:** requires map capability ≠ NONE. Returns `[]` for 600-series robots.

```json
[
  {
    "gx": 12,
    "gy": 8,
    "x_mm": 1800.0,
    "y_mm": 1200.0,
    "stuck_count": 5,
    "room_name": "Kitchen",
    "bearing_deg": 47,
    "distance_mm": 2160,
    "source": "stuck_events"
  }
]
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `gx`, `gy` | int | Yes — null for `robot_learned` | GridStore grid cell coordinates |
| `x_mm`, `y_mm` | float | Never | Dock-relative mm (pose space) |
| `stuck_count` | int | Yes — null for `robot_learned` | Accumulated stuck events in this cell |
| `room_name` | string | Yes — null without UMF alignment | Room from UmfAligner (confidence ≥ 0.70) |
| `bearing_deg` | int | Never | Compass bearing from dock |
| `distance_mm` | int | Never | Euclidean distance from dock |
| `source` | string | Never | `stuck_events` / `robot_learned` / `keepout` |

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=hazards"
```

---

### format=export

Full mission log as a versioned JSON bundle for backup and migration.

```json
{
  "export_version": 1,
  "exported_at": "2026-06-01T14:22:00Z",
  "blid": "abc123",
  "record_count": 810,
  "records": [...]
}
```

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

---

## POST /mission_history/import

Import a bundle produced by `format=export`. Records are deduplicated by `id` — existing records are never overwritten. Safe to run multiple times.

```
POST /api/roomba_plus/{entry_id}/mission_history/import
Content-Type: application/json
```

Body: the JSON produced by `format=export`.

```json
{
  "imported": 42,
  "skipped": 768,
  "errors": []
}
```

```bash
curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d @roomba_backup.json \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history/import"
```

---

## GET /household

Household-wide aggregate across all configured Roomba+ robots.

```
GET /api/roomba_plus/household?days=28
```

| Parameter | Type | Default | Range |
|---|---|---|---|
| `days` | int | 28 | 1–90 |

```json
{
  "period_days": 28,
  "total": {
    "missions": 47,
    "completed": 43,
    "completion_pct": 91.5,
    "area_sqft": 8960.0
  },
  "robots": [
    {
      "entry_id": "abc123",
      "name": "Roomba Downstairs",
      "floor": "Ground Floor",
      "missions": 31,
      "completed": 29,
      "completion_pct": 93.5,
      "area_sqft": 6200.0
    }
  ],
  "floors": [
    {
      "label": "Ground Floor",
      "missions": 31,
      "completed": 29,
      "area_sqft": 6200.0
    }
  ]
}
```

`floors` is omitted when no robot has a floor label configured. `area_sqft` is null when no robot has cloud records with area data.

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/household?days=14"
```

---

*[Roomba+](../README.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
