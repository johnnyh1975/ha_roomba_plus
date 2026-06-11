[← Roomba+](../README.md)

# Troubleshooting

---

## Setup & connection

**"Failed to connect" during setup**

Press the physical **Clean** button on the robot to start a manual cleaning job, then immediately retry credential retrieval in HA. Some models only respond while actively running.

If automatic pairing fails entirely: → [Retrieve iRobot credentials manually](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

---

**The iRobot app loses connection when Roomba+ is running**

Expected — the robot only allows one local MQTT connection. Either disable continuous mode in Settings → Roomba+ → Configure → Connection settings, or accept that the iRobot app will use the cloud path while Roomba+ is connected locally.

---

**Cloud authentication fails**

Check your iRobot app email and password. If you see an "mqtt slot" error, close the iRobot app on all devices and wait a few minutes before retrying.

---

**Cloud mission history not available for my Roomba 980**

Go to Settings → Roomba+ → Configure → iRobot cloud credentials, re-save your credentials, then restart HA.

---

## Zones & rooms

**Smart Map zones not appearing (i/s/j-series)**

Check that `"cap": {"pose": ...}` in the diagnostics download shows a value ≥ 1. If cloud credentials are configured, zone names come directly from the cloud and the naming repair flow is suppressed.

---

**Zone management — changes not reflected in dropdown immediately**

Alias and hidden changes are written immediately, but the zone select dropdown may take one MQTT message cycle to refresh. Typically resolves within seconds when the robot is active.

---

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

---

**`clean_room` or `vacuum.clean_area` raises `no_valid_segments` immediately after setup**

The active pmap ID may not yet be available — typically happens on fresh install or immediately after adding cloud credentials before the first cloud fetch completes. Wait a few minutes and retry. If it persists, check that `region_count_active` in diagnostics is > 0.

---

**"Map vacuum segments to areas" repair fires — what do I do?**

This is a one-time HA setup step for `vacuum.clean_area` (HA 2026.3+). Open the vacuum entity → ⚙ Entity settings → "Map vacuum segments to areas", match each robot room to a Home Assistant area, and save. See [Room cleaning setup](../README.md#room-cleaning-setup-ha-20263) in the README for full instructions.

If you don't use `vacuum.clean_area`, dismiss the repair — `roomba_plus.clean_room` works without it.

---

## Sensors showing Unknown

**`filter_last_replaced` / `brush_last_replaced` shows Unknown**

These sensors are Unknown until the first reset is performed. Press the reset button on the device page or call the reset action to populate them.

---

**Wear Intelligence sensors show Unknown**

Wear sensors need at least 3 days of mission data since the last reset to calculate a meaningful rate. They populate automatically.

---

**`optimal_clean_window` shows Unknown**

Requires at least 5 completed missions since integration setup. Updates automatically within minutes of the next mission end once 5 missions have recorded.

---

**Mission log sensors show Unknown after upgrading**

The mission log is populated going forward only. Streak, completion rate, and area sensors will be Unknown until the first mission completes after upgrading — this is expected.

---

**`last_error_code` shows a stale error after the robot has recovered**

The error state clears automatically when the next mission completes successfully. If it persists, restart HA to force re-reading the mission log from storage.

---

**Total energy consumed shows an unexpected value after upgrading to v2.5 on a Roomba 980**

Expected — v2.5 corrects the energy calculation for 900-series robots. The 980/985 firmware reports a raw BMS value approximately 3.73× the actual mAh; previous versions used this raw value directly. After upgrading, the sensor shows the correct lower value and continues accumulating from that point.

---

**Recent cleaned area / cleaning time show lower values than expected**

These sensors aggregate data from the iRobot API window (~30 recent missions). The iRobot API does not expose a lifetime accumulator for area or time — the `source: recent_mission_window` attribute documents this. The **total missions** sensor is different: it reads the lifetime counter embedded in every cloud record.

---

## Demand cleaning & scheduling

**`demand_clean_blocked` stays ON even though the robot is idle**

Check all four gates: (1) robot cycle state — `vacuum.{name}` must be `docked` or `idle`; (2) blocking sensors — any configured blocking sensor is ON; (3) presence — all tracked persons must be away if presence mode is `away_only`; (4) minimum gap — 6 hours must have elapsed since the last demand trigger. The `blocking_reason` attribute on the sensor names the active gate.

---

**Demand cleaning never triggers despite being enabled**

Check `binary_sensor.{name}_demand_clean_blocked` — it shows whether the robot is busy, a blocking sensor is active, or the 6-hour minimum gap has not elapsed. Also ensure cloud credentials are configured and at least 5 cloud mission records exist (check diagnostics for `region_count_active`).

---

**Demand cleaning triggers too often / not often enough**

Adjust the trigger multiplier in Configure → Demand cleaning. `1.5` (default) fires when dirt density is 50 % above the baseline for today's weekday. Lower the multiplier for more frequent triggers; raise it to require dirtier conditions. After v2.5 the baseline is weekday-specific — Monday's threshold is set by Monday's history — so the multiplier applies relative to each day's normal level.

---

**Self-calibrating filter/brush thresholds: when do they activate?**

After two or more resets of a given component, Roomba+ computes the median interval between resets and uses that as the effective threshold. Until two resets have been performed, the configured threshold is used. The learned values are visible in diagnostics under `learned_maintenance`.

---

**Presence-aware scheduling step not visible in options menu**

The presence scheduling step only appears for robots that report `schedHold` in their MQTT state (i/s/j/Braava m6). It will not appear for 900-series or 600-series robots.

---

**`smart_start` queues forever / never starts**

Check that the blocking sensors are reporting correctly. Unavailable or unknown sensors are treated as non-blocking. If the queue expires, `roomba_plus_start_timeout` is fired — automate on this event to alert or retry.

---

## Mission anomaly detection

**"Unusual cleaning patterns" Repair Issue fires for normal short cleans**

The anomaly detection (v2.5+) uses your robot's personal performance history as the baseline. If the flag fires for a normal targeted single-room clean, the single-room area is far smaller than your typical full-home baseline — which is technically correct. The issue self-resolves: if the next mission is normal, the counter resets and the issue clears. Two consecutive anomalous missions are required to fire the issue.

---

## Cloud & history

**Mission history export**

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

**Mission history import**

```bash
curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d @roomba_backup.json \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history/import"
```

Import deduplicates by `id` — safe to run multiple times. Returns `{"imported": N, "skipped": N, "errors": []}`.

The `entry_id` is found in Settings → Devices → Roomba+ → ⋮ → System information.

---

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

---

*[Roomba+](../README.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
