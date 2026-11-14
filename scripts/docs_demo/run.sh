#!/usr/bin/env bash
# Regenerate the screenshots in docs/images from a demo Home Assistant with
# a simulated Roomba 980. See README.md in this folder for what it needs.
#
#   bash scripts/docs_demo/run.sh [demo_dir]
#
# DEMO_PY    Python with homeassistant (2026.11 or later), roombapy,
#            roombapy-prime, playwright and Pillow
# BROKER_PY  Python with amqtt
# CHROME     a Chrome or Chromium binary (default /usr/bin/google-chrome)
#
# It starts three processes (broker, robot, Home Assistant) and stops them
# when it ends. Nothing leaves this machine: the robot, the broker and Home
# Assistant all listen on 127.0.0.1.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEMO="$(mkdir -p "${1:-/tmp/roomba_plus_demo}" && cd "${1:-/tmp/roomba_plus_demo}" && pwd)"
PY="${DEMO_PY:?set DEMO_PY}"
BPY="${BROKER_PY:?set BROKER_PY}"
export CHROME="${CHROME:-/usr/bin/google-chrome}"
GRID="$REPO/tests/fixtures/roomba_plus_grid_02_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"
HASS="$(dirname "$PY")/hass"

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

wait_http() { for _ in $(seq 1 120); do curl -s -o /dev/null "$1" && return 0; sleep 2; done; echo "no answer from $1"; exit 1; }
start_ha() { "$HASS" -c "$DEMO/config" --skip-pip-packages roomba_plus > "$DEMO/ha.log" 2>&1 & pids+=($!); wait_http http://127.0.0.1:8123/; }
stop_ha() { kill "${pids[-1]}"; wait "${pids[-1]}" 2>/dev/null || true; unset 'pids[-1]'; }

echo "== demo in $DEMO"
rm -rf "$DEMO/config" "$DEMO/shots" "$DEMO/token.json"
mkdir -p "$DEMO/config/custom_components"
ln -s "$REPO/custom_components/roomba_plus" "$DEMO/config/custom_components/roomba_plus"
cat > "$DEMO/config/configuration.yaml" <<'EOF'
default_config:
homeassistant:
  name: Demo Home
  unit_system: metric
  time_zone: Europe/Berlin
  country: DE
  language: en
  currency: EUR
  latitude: 52.52
  longitude: 13.40
  elevation: 34
logger:
  default: warning
  logs:
    custom_components.roomba_plus: info
EOF
[ -f "$DEMO/cert.pem" ] || openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=roomba" \
  -keyout "$DEMO/key.pem" -out "$DEMO/cert.pem" 2>/dev/null

echo "== broker and robot"
"$BPY" "$HERE/broker.py" "$DEMO/cert.pem" "$DEMO/key.pem" > "$DEMO/broker.log" 2>&1 & pids+=($!)
sleep 3
# 0.3 s a step makes a clean of about two minutes.
DEMO_GRID="$GRID" DEMO_STEP="${DEMO_STEP:-0.3}" "$PY" "$HERE/robot.py" > "$DEMO/robot.log" 2>&1 & pids+=($!)

echo "== Home Assistant: first start and onboarding"
start_ha
"$PY" "$HERE/demo.py" onboard "$DEMO"
sleep 15  # Home Assistant writes the new user and token with a delay
stop_ha

echo "== the robot's entry and history"
"$PY" "$HERE/demo.py" seed "$DEMO"
start_ha
"$PY" "$HERE/demo.py" wait-docked "$DEMO"
"$PY" "$HERE/demo.py" dashboard "$DEMO"
"$PY" "$HERE/demo.py" name-rooms "$DEMO"

echo "== three missions, so the map has its walls and the history its numbers"
for _ in 1 2 3; do
  "$PY" "$HERE/demo.py" start "$DEMO"
  "$PY" "$HERE/demo.py" wait-docked "$DEMO"
  # A start soon after docking reads as the same mission resumed after a
  # recharge; a real robot rests on the dock longer than this.
  sleep 90
done

echo "== screenshots"
"$PY" "$HERE/demo.py" start "$DEMO"
sleep 45
"$PY" "$HERE/demo.py" shots "$DEMO" dashboard live_map
"$PY" "$HERE/demo.py" wait-docked "$DEMO"
"$PY" "$HERE/demo.py" shots "$DEMO" device calendar options_menu options_zones options_settings coverage_map
"$PY" "$HERE/demo.py" images "$DEMO" "$REPO/docs/images"

if grep -E "ERROR|Detected blocking" "$DEMO/ha.log" | grep -i roomba_plus; then
  echo "!! Roomba+ logged errors in the demo, see $DEMO/ha.log"
fi
echo "== done: $REPO/docs/images"
