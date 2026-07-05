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
| `GET` | `/api/roomba_plus/{entry_id}/mission_history` | Mission history (summary / records / hazards / export / zone_coverage_health) |
| `POST` | `/api/roomba_plus/{entry_id}/mission_history/import` | Import a history export bundle |
| `GET` | `/api/roomba_plus/{entry_id}/digest` | One-day summary for a single robot (v2.9.0) |
| `GET` | `/api/roomba_plus/household` | Household aggregate across all robots |
| `GET` | `/api/roomba_plus/{entry_id}/mission/{mission_id}/explain` | Anomaly explanation for a specific mission (v3.2.0) |
| `GET` | `/api/roomba_plus/{entry_id}/mission/{n_mssn}/path` | Room-granular path reconstruction for a mission (v3.2.0) |

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

### format=zone_coverage_health

Per-room cleaning-cadence health, self-calibrated against each room's own historical rhythm — not a fixed schedule. A room is `overdue` only relative to its own typical interval between cleans, learned from `last_cleaned_rooms` timestamps over the last 90 days.

**Gate:** requires cloud enrichment (`last_cleaned_rooms` data) — returns `{}` for 600-series robots or before enough room-tagged mission history exists.

```json
{
  "Kitchen": {
    "days_since_last": 21.0,
    "expected_interval_days": 7.0,
    "status": "overdue"
  },
  "Bedroom": {
    "days_since_last": 3.0,
    "expected_interval_days": null,
    "status": "insufficient_data"
  }
}
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `days_since_last` | float | Never | Days since this room's last recorded clean |
| `expected_interval_days` | float | Yes — while under 3 recorded intervals | This robot's own mean interval between cleans of this room |
| `status` | string | Never | `healthy` / `overdue` / `insufficient_data` |

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=zone_coverage_health"
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

## GET /digest

Compact one-day summary for a single robot — built for a card's "Today" status slot.

```
GET /api/roomba_plus/{entry_id}/digest?date=2026-06-16
```

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `date` | string (`YYYY-MM-DD`) | today | Robot's local timezone |

```json
{
  "missions": 2,
  "area_m2": 72.1,
  "stuck_events": 0,
  "demand_cleans": 1,
  "filter_hours_today": 1.4,
  "battery_cycles_today": 2
}
```

`filter_hours_today` and `battery_cycles_today` are the genuine increase in
the robot's lifetime hour/charge-cycle counters attributable to that
specific day — not an estimate. Both are `null` (never a guessed `0`) when
there's no earlier mission to compare against, e.g. the very first day this
robot has any recorded history.

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/digest?date=2026-06-16"
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

## GET /mission/{mission_id}/explain

Rule-based explanation for why a mission was (or wasn't) flagged anomalous — same logic as the `roomba_plus.explain_mission` service, exposed as REST for external tooling. `{mission_id}` accepts `latest` to explain the most recent mission regardless of whether it was anomalous.

```
GET /api/roomba_plus/{entry_id}/mission/{mission_id}/explain
GET /api/roomba_plus/{entry_id}/mission/latest/explain
```

```json
{
  "mission_id": "m_20260616_0912",
  "is_anomalous": true,
  "anomaly_reason": "obstacle_or_blockage",
  "robot_lifted": false,
  "error_code": null,
  "recommended_action": "Check for an obstacle or narrow gap the robot may be stuck navigating around."
}
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `anomaly_reason` | string | Yes — when not anomalous | `obstacle_or_blockage` / `excessive_recharge` / `dirt_spike` / `incomplete_coverage` |
| `robot_lifted` | bool | Never | `bbrun.nPicks` increased during this mission — independent of `anomaly_reason` |
| `error_code` | int | Yes | This mission's own recorded error, if any — independent of `anomaly_reason` |
| `recommended_action` | string | Yes — when not anomalous | Plain-language suggestion matching `anomaly_reason` |

`robot_lifted` and `error_code` are reported alongside `anomaly_reason`, not folded into it — a mission can be lifted and/or error-coded regardless of whether any of the four anomaly reasons also applies.

Returns `404` if `mission_id` doesn't match any recorded mission.

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission/latest/explain"
```

---

## GET /mission/{n_mssn}/path

Room-granular post-hoc reconstruction of a mission's path — not pixel-accurate pose tracking. Consecutive visits to the same room collapse into a single timeline entry (arrival time, not every individual room re-entry event).

```
GET /api/roomba_plus/{entry_id}/mission/{n_mssn}/path
```

`{n_mssn}` is the robot's own mission number (`nMssn`), not the local `id` used elsewhere in this API — this endpoint is cloud-history-backed (`MissionArchive`), matching how the robot itself numbers missions.

```json
{
  "nMssn": 102,
  "path": [
    {"room": "Kitchen", "time": "2026-06-16T09:05:12Z"},
    {"room": "Hallway", "time": "2026-06-16T09:23:40Z"},
    {"room": "Bedroom", "time": "2026-06-16T09:31:05Z"}
  ]
}
```

| Field | Type | Null? | Notes |
|---|---|---|---|
| `room` | string | Never — falls back to the raw region id | Resolved from cloud region names (SMART) or RoomSegStore names (EPHEMERAL) |
| `time` | string | Never | ISO timestamp of arrival in that room |

**Gate:** requires `mission_archive` (cloud enrichment). Returns `404` if `n_mssn` doesn't match any archived mission, or if no mission archive is available at all.

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission/102/path"
```

---

## GET /missions/{record_id}/map.json  ·  /map.png

**v3.3.0 MISSION-MAP** — coordinate-level coverage of one finished mission: the
official app's "Mission Cleaning Map", for every mission in your history.
SMART robots with cloud credentials only; requires the mission record to carry
`pmaps_info` (recorded on v3.3.0+, cloud-merged).

```
GET /api/roomba_plus/{entry_id}/missions/{record_id}/map.json
GET /api/roomba_plus/{entry_id}/missions/{record_id}/map.png
```

`{record_id}` is the local mission record id; `latest` resolves to the most
recent mission. The PNG is directly usable as a picture-card or notification
image — room outlines (current map) plus this mission's real coverage points.

```json
{
  "record_id": "m_1751623200",
  "mission_id": "01HB240BER0YBZEERTM7D3QHT8",
  "nmssn": 90,
  "pmap_id": "…", "pmapv_id": "…",
  "point_area_m": [0.1049, 0.1049],
  "coverage_mm": [[1049.0, 2098.0], …],
  "coverage_poly": [...],
  "rooms": {"Kitchen": [[x_mm, y_mm], …], …}
}
```

| Status | Meaning |
|---|---|
| 404 | Record unknown; record has no `pmaps_info` (EPHEMERAL, or pre-v3.3.0 record); or the cloud returned no coverage layer (currently an open question on i-series/lewis firmware — confirmed working on j-series/sapphire) |
| 409 | Verification-gate mismatch — the cloud map's `map_header.nmssn` does not match the requested record; the wrong map is never served silently |
| 502 | Cloud transport failure |

Results are cached in memory for 24 h (max 10 missions) — repeated card or
browser hits cost no additional cloud call.

---

*[Roomba+](../README.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
