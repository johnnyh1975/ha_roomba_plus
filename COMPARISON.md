<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Roomba Integrations — Feature Comparison</title>
<style>
  :root {
    --bg: #ffffff;
    --bg-secondary: #f8f8f6;
    --bg-header: #f1f0eb;
    --text: #1a1a18;
    --text-muted: #5f5e5a;
    --text-faint: #888780;
    --border: rgba(0,0,0,0.10);
    --border-strong: rgba(0,0,0,0.18);
    --green: #3B6D11;
    --green-bg: #EAF3DE;
    --amber: #854F0B;
    --amber-bg: #FAEEDA;
    --red: #A32D2D;
    --red-bg: #FCEBEB;
    --blue: #185FA5;
    --blue-bg: #E6F1FB;
    --teal: #0F6E56;
    --teal-bg: #E1F5EE;
    --section-bg: #f1f0eb;
    --radius: 10px;
    --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 0 0 0.5px rgba(0,0,0,0.08);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1e1e1c;
      --bg-secondary: #252523;
      --bg-header: #2c2c2a;
      --text: #e8e6df;
      --text-muted: #a8a79f;
      --text-faint: #686860;
      --border: rgba(255,255,255,0.10);
      --border-strong: rgba(255,255,255,0.18);
      --green: #97C459;
      --green-bg: #173404;
      --amber: #EF9F27;
      --amber-bg: #412402;
      --red: #F09595;
      --red-bg: #501313;
      --blue: #85B7EB;
      --blue-bg: #042C53;
      --teal: #5DCAA5;
      --teal-bg: #04342C;
      --section-bg: #2c2c2a;
      --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 0 0 0.5px rgba(255,255,255,0.06);
    }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    color: var(--text);
    background: var(--bg);
    padding: 2rem 1rem;
  }
  .page { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 6px; }
  .subtitle { font-size: 13px; color: var(--text-muted); margin-bottom: 2rem; }
  .legend {
    display: flex; gap: 20px; flex-wrap: wrap;
    margin-bottom: 1.5rem;
    font-size: 12px; color: var(--text-muted);
  }
  .legend-item { display: flex; align-items: center; gap: 6px; }
  .table-wrap {
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    min-width: 680px;
    background: var(--bg);
  }
  thead th {
    background: var(--bg-header);
    padding: 12px 14px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border-strong);
    white-space: nowrap;
  }
  thead th.feat { min-width: 190px; }
  thead th.center { text-align: center; }
  tbody td {
    padding: 9px 14px;
    border-bottom: 0.5px solid var(--border);
    vertical-align: top;
    font-size: 13px;
    line-height: 1.45;
  }
  tbody td.feat {
    font-weight: 500;
    color: var(--text-muted);
    font-size: 12px;
  }
  tbody td.center { text-align: center; }
  tr.section td {
    background: var(--section-bg);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--text-faint);
    padding: 6px 14px;
    border-bottom: 0.5px solid var(--border);
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td:not(.section-cell) { background: var(--bg-secondary); }
  .yes { color: var(--green); font-weight: 500; }
  .partial { color: var(--amber); font-weight: 500; }
  .no { color: var(--red); }
  .best { color: var(--blue); font-weight: 500; }
  .note { font-size: 11px; color: var(--text-faint); }
  .badge {
    display: inline-block; font-size: 10px; font-weight: 600;
    padding: 2px 8px; border-radius: 20px; white-space: nowrap;
  }
  .b-core { background: var(--blue-bg); color: var(--blue); }
  .b-hacs { background: var(--green-bg); color: var(--green); }
  .b-cloud { background: var(--amber-bg); color: var(--amber); }
  .footnote {
    margin-top: 1.5rem;
    font-size: 11px;
    color: var(--text-faint);
    line-height: 1.7;
    border-top: 0.5px solid var(--border);
    padding-top: 1rem;
  }
  code {
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 11px;
    background: var(--bg-secondary);
    padding: 1px 5px;
    border-radius: 4px;
    border: 0.5px solid var(--border);
  }
  .col-core { background: rgba(24,95,165,0.04); }
  .col-rp   { background: rgba(15,110,86,0.04); }
  .col-rr   { background: rgba(186,117,23,0.04); }
</style>
</head>
<body>
<div class="page">

<h1>Roomba Integrations — Feature Comparison</h1>
<p class="subtitle">Based on source code analysis · May 2026 · All three main integration paths for iRobot robots in Home Assistant</p>

<div class="legend">
  <div class="legend-item"><span style="color:var(--green);font-size:16px">✅</span> Supported</div>
  <div class="legend-item"><span style="color:var(--amber);font-size:16px">⚠️</span> Partial / limited</div>
  <div class="legend-item"><span style="color:var(--red);font-size:16px">❌</span> Not available</div>
  <div class="legend-item"><span style="color:var(--blue);font-size:16px">★</span> Best in class</div>
</div>

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th class="feat">Feature</th>
      <th class="center col-core">HA Core<br><span class="badge b-core">Built-in</span></th>
      <th class="center col-rp">Roomba+<br><span class="badge b-hacs">HACS</span></th>
      <th class="center col-rr">roomba_rest980<br><span class="badge b-hacs">HACS</span></th>
    </tr>
  </thead>
  <tbody>

    <!-- BASICS -->
    <tr class="section"><td colspan="4" class="section-cell">Basics</td></tr>

    <tr>
      <td class="feat">Connection type</td>
      <td><span class="yes">✅ Local</span><br><span class="note">MQTT/TLS via roombapy</span></td>
      <td><span class="yes">✅ Local</span> <span style="color:var(--blue)">★</span><br><span class="note">MQTT/TLS via roombapy</span></td>
      <td><span class="partial">⚠️ HTTP polling</span><br><span class="note">REST calls to rest980 container</span></td>
    </tr>
    <tr>
      <td class="feat">Push vs. poll</td>
      <td><span class="yes">✅ Push</span><br><span class="note">MQTT events</span></td>
      <td><span class="yes">✅ Push</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="partial">⚠️ Poll</span><br><span class="note">every N seconds</span></td>
    </tr>
    <tr>
      <td class="feat">External prerequisites</td>
      <td><span class="yes">✅ None</span></td>
      <td><span class="yes">✅ None</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌ Docker required</span><br><span class="note">rest980 Node.js container must run 24/7</span></td>
    </tr>
    <tr>
      <td class="feat">Cloud-free operation</td>
      <td><span class="yes">✅ Fully local</span></td>
      <td><span class="yes">✅ Fully local</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="partial">⚠️ Partial</span><br><span class="note">map + zone selection requires cloud (pmaps)</span></td>
    </tr>
    <tr>
      <td class="feat">iRobot cloud dependency</td>
      <td><span class="yes">✅ None</span></td>
      <td><span class="yes">✅ None</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌ Required for key features</span><br><span class="note">Gigya auth unstable since Oct 2024; iRobot sold to Picea Jan 2026</span></td>
    </tr>
    <tr>
      <td class="feat">Setup effort</td>
      <td><span class="yes">✅ Low</span><br><span class="note">DHCP/Zeroconf discovery</span></td>
      <td><span class="yes">✅ Low</span><br><span class="note">DHCP/Zeroconf discovery</span></td>
      <td><span class="no">❌ High</span><br><span class="note">Docker, Node.js, manual credential config</span></td>
    </tr>
    <tr>
      <td class="feat">Supported models</td>
      <td>690, 890, 960, 980, s9+, Braava m6</td>
      <td><span style="color:var(--blue)">★</span> 600–900, i, s, j, Braava m6</td>
      <td>i7+, s9+ focus</td>
    </tr>
    <tr>
      <td class="feat">x05 models (105/405/505)</td>
      <td><span class="no">❌</span></td>
      <td><span class="no">❌</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Unit tests</td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">133 tests</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Translations</td>
      <td><span class="partial">⚠️ EN only</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">DE + EN</span></td>
      <td><span class="partial">⚠️ EN only</span></td>
    </tr>

    <!-- SENSORS -->
    <tr class="section"><td colspan="4" class="section-cell">Sensors</td></tr>

    <tr>
      <td class="feat">Total sensor count</td>
      <td>13</td>
      <td><span style="color:var(--blue)">★</span> 35</td>
      <td>27</td>
    </tr>
    <tr>
      <td class="feat">Battery</td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span> + dynamic icon + <code>batInfo</code> attributes</td>
    </tr>
    <tr>
      <td class="feat">Phase / status</td>
      <td><span class="partial">⚠️</span><br><span class="note">via vacuum activity only</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">own sensor + Idle/Stopped detection</span></td>
      <td><span class="yes">✅</span><br><span class="note">Idle/Stopped detection, own sensor</span></td>
    </tr>
    <tr>
      <td class="feat">Error code (80+ codes)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Readiness / not-ready</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Job initiator</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Next scheduled clean</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">cleanSchedule + cleanSchedule2</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Mission statistics</td>
      <td><span class="partial">⚠️</span><br><span class="note">total, ok, failed</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">+ cancelled, avg time, cleaned area</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">total jobs only</span></td>
    </tr>
    <tr>
      <td class="feat">Mission start timestamp (active)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">available only during active mission</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Mission elapsed time</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Mission recharge / expire time</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Maintenance sensors (filter / brushes)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">hours remaining + reset buttons with persistence</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Navigation quality (<code>l_squal</code>)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">opt-in, VSLAM robots only</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Wi-Fi signal (RSSI / SNR / Noise)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">all three, opt-in</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">all three, enabled by default</span></td>
    </tr>
    <tr>
      <td class="feat">IP address</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">opt-in</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Carpet Boost mode (readable)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">Eco / Performance / Auto</span></td>
      <td><span class="yes">✅</span><br><span class="note">Eco / Performance / Auto</span></td>
    </tr>
    <tr>
      <td class="feat">Clean mode / passes (readable)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Edge cleaning (readable)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Clean Base status</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span><br><span class="note">6 detailed states</span></td>
    </tr>
    <tr>
      <td class="feat">Mop sensors (Braava m6)</td>
      <td><span class="no">❌</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">combined mop_ready binary sensor</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">4 sensors: tank, pad, tank level, clean mode</span></td>
    </tr>
    <tr>
      <td class="feat">Raw state attributes sensor</td>
      <td><span class="no">❌</span></td>
      <td><span class="no">❌</span><br><span class="note">diagnostics download only</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">local + cloud raw attribute sensors</span></td>
    </tr>
    <tr>
      <td class="feat">Cloud pmap sensor</td>
      <td><span class="no">❌</span></td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">one sensor per saved map</span></td>
    </tr>

    <!-- CONTROLS -->
    <tr class="section"><td colspan="4" class="section-cell">Controls</td></tr>

    <tr>
      <td class="feat">Start / Stop / Pause / Return</td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
    </tr>
    <tr>
      <td class="feat">Cleaning passes (writable)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Select entity, fully local</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">via rest980 API only, no HA entity</span></td>
    </tr>
    <tr>
      <td class="feat">Edge cleaning (writable)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Switch entity, fully local</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">via rest980 API only, no HA entity</span></td>
    </tr>
    <tr>
      <td class="feat">Always finish (binPause)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Switch entity, fully local</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">via rest980 API only, no HA entity</span></td>
    </tr>
    <tr>
      <td class="feat">Schedule hold (schedHold)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Switch entity</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Carpet Boost (writable)</td>
      <td><span class="yes">✅</span><br><span class="note">via fan_speed on 980</span></td>
      <td><span class="yes">✅</span><br><span class="note">Switch (980) + fan_speed</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">via rest980 API only, no HA entity</span></td>
    </tr>
    <tr>
      <td class="feat">Repeat last mission</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Button entity, local</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Locate robot</td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Evacuate Clean Base</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Maintenance reset (filter / brushes / battery)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Button entities with hass.storage persistence</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Favorites / cloud routines</td>
      <td><span class="no">❌</span></td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Button per favorite from cloud API</span></td>
    </tr>

    <!-- MAP & ZONES -->
    <tr class="section"><td colspan="4" class="section-cell">Map &amp; Zones</td></tr>

    <tr>
      <td class="feat">Live cleaning map</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">ImageEntity, inline in popup</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Camera entity with real room names from cloud pmaps</span></td>
    </tr>
    <tr>
      <td class="feat">Map survives HA restart</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">hass.storage persistence</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Zone / room selection</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span><br><span class="note">local via region_id</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">Select per room/zone with real names from cloud</span></td>
    </tr>
    <tr>
      <td class="feat">Zone selection — fully local (no cloud)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌</span><br><span class="note">pmap sync requires cloud</span></td>
    </tr>
    <tr>
      <td class="feat">Real room names</td>
      <td><span class="no">❌</span></td>
      <td><span class="partial">⚠️</span><br><span class="note">manually named via Repair Issue</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">directly from cloud pmaps</span></td>
    </tr>
    <tr>
      <td class="feat">Automatic room detection (900-series)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">gap segmentation, EMA confidence</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Door-width calibration</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌</span></td>
    </tr>

    <!-- HA INTEGRATION -->
    <tr class="section"><td colspan="4" class="section-cell">HA Integration Quality</td></tr>

    <tr>
      <td class="feat">Device triggers</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">6 triggers: started, finished, stuck, bin full, docked, error</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Repair Issues</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">zone naming, Smart Map zone prompts</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Diagnostics download</td>
      <td><span class="partial">⚠️</span><br><span class="note">basic</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">includes map + zone state</span></td>
      <td><span class="no">❌</span></td>
    </tr>
    <tr>
      <td class="feat">Multi-Roomba support</td>
      <td><span class="yes">✅</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span><br><span class="note">BLID-based, separate stores per entry</span></td>
      <td><span class="partial">⚠️</span></td>
    </tr>
    <tr>
      <td class="feat">Entity grouping (Steuerelemente / Diagnose)</td>
      <td><span class="no">❌</span></td>
      <td><span class="yes">✅</span> <span style="color:var(--blue)">★</span></td>
      <td><span class="no">❌</span></td>
    </tr>

  </tbody>
</table>
</div>

<div class="footnote">
  <sup>1</sup> roomba_rest980 requires a permanently running Docker container (rest980 + Node.js). All cloud-dependent features (map with room names, zone selection) rely on iRobot's Gigya authentication, which has been unstable since October 2024. iRobot was acquired by Picea Robotics in January 2026.<br>
  <sup>2</sup> x05-series models (Roomba 105, 405, 505) use a different protocol and are not supported by any of the three integrations.<br>
  <sup>3</sup> "Best in class" (★) indicates the integration that offers the most complete or native implementation for that feature. Multiple integrations can share ★ where equally capable.<br>
  <sup>4</sup> roomba_rest980 controls (cleaning passes, edge cleaning, always finish, carpet boost) are available via the rest980 REST API but have no corresponding HA entity — they cannot be used in the HA Automation editor without custom scripts.
</div>

</div>
</body>
</html>