# Roomba+ v2.0.0

**Complete cloud diagnostics · Gold Quality Scale · Architecture refactor**

---

## What's new

### Cloud diagnostics — all robots with iRobot credentials

v2.0 treats each `/missionhistory` record as a first-class data object. Six new sensors are now derived from the per-mission cloud record list (approx. 30-day window):

| Sensor | Description |
|---|---|
| **Recent completion rate** | % of missions completed |
| **Recent mid-mission recharges** | Total `chrgs` events |
| **Recent Clean Base evacuations** | Total `evacs` events |
| **Recent dirt events** | Total `dirt` detection events |
| **Recent error code (cloud)** | `pauseId` from the most recent failed mission — more reliable than MQTT, includes `label` / `description` / `action` attributes from the iRobot error catalogue |
| **Recent error time (cloud)** | Timestamp of the most recent failed mission |

All six sensors group under the `recent_` prefix in the diagnostic category, cleanly separated from the existing MQTT-sourced `Last error code` / `Last error at` sensors.

### 900-series timestamp backfill

The 980/900-series firmware resets `mssnStrtTm=0` in the mission-end MQTT message, causing local mission log records to show `duration=0` and an incorrect `started_at`. v2.0 automatically corrects these records on startup using the authoritative `startTime`/`timestamp` fields from the cloud — no user action required.

### REST API — per-mission records

The history endpoint now supports a second response format for the Lovelace card and custom dashboards:

```
GET /api/roomba_plus/{entry_id}/mission_history?format=records
```

Returns a unified per-mission array. Cloud robots include `run_min`, `recharges`, `evacuations`, `dirt_events`, `wifi_signal`. All robots include `started_at`, `ended_at`, `duration_min`, `area_sqft`, `result`, `initiator`, `zones`, `error_code`, `source`.

The default `format=summary` response is unchanged — the v0.1-beta Lovelace card continues to work without any update.

### Bug fix — mid-mission recharge sensor on i/s/j-series robots

`sensor.mission_recharge_minutes` and `sensor.mission_expire_minutes` were always unavailable on i7, s9+, and j-series robots. lewis firmware sends `rechrgTm` / `expireTm` as Unix timestamps and leaves `rechrgM` / `expireM` at zero — the sensors only read the pre-computed fields and silently returned nothing. Both sensors now compute remaining time from the timestamp when the pre-computed field is absent. 980/900-series behaviour unchanged.

---

### Reconfiguration flow

Host IP address and password can now be changed without removing and re-adding the integration. Go to **Settings → Devices → Roomba+ → ⋮ → Reconfigure**. The new connection is validated before any changes are applied.

### Architecture refactor

`__init__.py` (1136 lines) split into three focused modules:

- `callbacks.py` — MQTT mission lifecycle tracking and MissionStore recording
- `services.py` — all six service/action handlers and registration
- `__init__.py` — setup, teardown, connection helpers (388 lines)

No behaviour change. All existing automations, dashboards, and entity IDs are unaffected.

---

## Migration from v1.9.0

**Automatic — no action required.**

On first boot after upgrading, HA detects the config entry version change (1 → 2) and runs `async_migrate_entry`. This adds a single marker key to the entry options (`cloud_raw_records_version: 1`). All existing data is preserved:

- Zone names and management settings ✅
- Maintenance baselines (filter, brush, battery, pad) ✅
- Blocking sensor configuration ✅
- Presence scheduling configuration ✅
- Mission log (hass.storage) ✅

---

## Quality Scale

**Gold** — all 40 applicable rules satisfied. The only open item is `brands` (requires a manual PR to home-assistant/brands after release).

| Level | Status |
|---|---|
| Bronze | ✅ Complete |
| Silver | ✅ Complete |
| Gold | ✅ Complete |
| Platinum | ✅ Complete (all 4 Platinum rules satisfied) |

New in this release: `icon-translations` (98 entity icons moved to `icons.json`), `reconfiguration-flow`, `stale-devices`, `strict-typing`, `migration`, `config-flow-test-coverage`.

---

## Tests

| Suite | Count | Framework |
|---|---|---|
| Unit tests (`tests/`) | 1042 | Stub-based, no HA install required |
| Integration tests (`tests_integration/`) | 11 | `pytest-homeassistant-custom-component` |
| **Total** | **1053** | |

892 tests shipped in v1.9.0. 161 new tests added in v2.0.

---

## Breaking changes

None. All entity IDs, service names, and REST API defaults are unchanged.

The `format=records` endpoint is additive. The default `format=summary` response shape is identical to v0.1-beta.

---

## Full changelog

**Added**
- `classify_mission_result()` — canonical cloud result classifier (`completed` / `cancelled_by_user` / `cancelled` / `error_{pauseId}` / `stuck` / `unknown`)
- `IrobotCloudCoordinator.raw_records` property — per-mission record list with pre-computed `classified_result`
- `MissionStore.backfill_from_cloud()` — corrects 900-series timestamp records from cloud data
- `async_migrate_entry` — config entry v1 → v2 migration
- `async_step_reconfigure` — host/password change without re-adding
- `async_remove_config_entry_devices` — stale device cleanup
- `icons.json` — 98 entity icons (sensor 75, button 14, select 6, switch 3)
- `callbacks.py` — extracted from `__init__.py`
- `services.py` — extracted from `__init__.py`
- 6 new `CloudRawSensor` entities
- `GET /mission_history?format=records` endpoint

**Changed**
- `__init__.py` refactored from 1136 → 388 lines
- `cloud_coordinator.py` — stores `mission_history_raw` alongside aggregates
- `api_views.py` — `format` query parameter dispatch
- All entity `icon=` / `_attr_icon` assignments removed (now served from `icons.json`)
- Config entry version bumped from 1 → 2
- Manifest version bumped to `2.0.0`
- Quality Scale badge updated to Gold

**Fixed**
- `sensor.mission_recharge_minutes` and `sensor.mission_expire_minutes` were always unavailable on i/s/j-series robots. lewis firmware (`i755840`, `s9+`, `j7`) sets `rechrgM=0` / `expireM=0` and sends `rechrgTm` / `expireTm` as Unix timestamps instead. Both sensors now fall back to computing remaining minutes from the timestamp when the pre-computed field is zero. Confirmed against field diagnostics (Bogdana, i755840, `rechrgTm=1780150205`). 980/900-series behaviour unchanged.
- 23 missing return type annotations across `sensor.py`, `button.py`, `select.py`, `blocking_manager.py`
- Incorrect Pillow `get_flattened_data()` call in map renderer test (API removed in newer Pillow)
