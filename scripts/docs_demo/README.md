# The documentation demo

The screenshots in [`docs/images`](../../docs/images) come from a Home
Assistant with a simulated Roomba 980, made by `run.sh` in this folder. Run it
again after a change to the interface, and the pictures follow.

```bash
DEMO_PY=<venv>/bin/python BROKER_PY=<venv2>/bin/python \
  bash scripts/docs_demo/run.sh /tmp/roomba_plus_demo
```

It takes about twenty minutes, most of them four simulated cleans of two minutes each with a rest on the dock between them, and writes the eight pictures of `docs/images`.
Look at them before committing: the clicks in the Configure dialog hit fixed
positions, and a Home Assistant release that moves a button moves the picture
too.

## What it needs

| | |
|---|---|
| `DEMO_PY` | A Python 3.14 environment with `homeassistant` 2026.11 or later, `home-assistant-frontend`, `roombapy>=2.0.2`, `roombapy-prime[map]` as pinned in the manifest, `playwright` and `Pillow`. Playwright needs no browser download: it uses `CHROME`. |
| `BROKER_PY` | A separate environment with `amqtt`. Its dependencies clash with Home Assistant's, so it does not share `DEMO_PY`. |
| `CHROME` | A Chrome or Chromium binary, `/usr/bin/google-chrome` by default. |
| `openssl` | For the robot's self-signed certificate. |

Home Assistant installs the packages of its own default integrations at the
first start, so that start needs PyPI. Nothing else leaves the machine: the
robot, its broker and Home Assistant listen on `127.0.0.1` only.

## How it works

| File | Plays |
|---|---|
| `broker.py` | The robot's own MQTT broker, TLS on port 8883, with `amqtt`. |
| `robot.py` | A Roomba 980: its reported state, the answers to `start`, `pause`, `dock` and the other commands, and a cleaning path over the floor of the test fixtures' coverage grid. |
| `demo.py` | The steps: onboarding, the robot's entry and stored history, the dashboard, naming the rooms, starting a clean, the screenshots, and cropping them. |
| `run.sh` | All of it in order. |

The entry, missions, coverage and rooms come from the anonymised fixtures in
`tests/fixtures`, moved in time so the newest mission ended yesterday. The
robot id, addresses, MAC and account are the project's placeholders.

Two departures from a real robot, both deliberate:

- `robot.py` publishes on `aws/things/<blid>/shadow/update`. The robot uses a
  topic that starts with `$`, which `amqtt` refuses, and roombapy subscribes to
  `#` and does not look at the topic.
- It sends its whole state every eight seconds while docked, so that Home
  Assistant gets it whenever it connects. A real robot sends it on connect.

The robot answers the setup's discovery probe only with `DEMO_DISCOVERY=1`:
Home Assistant's discovery binds the same UDP port, and both cannot run on one
machine. `run.sh` writes the entry directly instead of going through setup.

## Steering the robot by hand

```bash
$DEMO_PY scripts/docs_demo/demo.py poke demo/set '{"batPct": 40}'
$DEMO_PY scripts/docs_demo/demo.py poke demo/full
```

`demo/set` publishes part of the state, `demo/full` all of it. With `run.sh`
stopped after the first screenshots, the Home Assistant in the demo folder is
an ordinary one: log in as `demo` with the password `demo-demo-demo` at
`http://127.0.0.1:8123`.
