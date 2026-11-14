"""The documentation demo: one Home Assistant, one simulated Roomba 980.

Steps for run.sh, each a subcommand. Run with the Python that has Home
Assistant, roombapy, roombapy-prime, playwright and Pillow installed.

    python demo.py seed <demo_dir>        config entry and stored history
    python demo.py onboard <demo_dir>     first user and an access token
    python demo.py dashboard <demo_dir>   the dashboard of the screenshots
    python demo.py name-rooms <demo_dir>  name the rooms the robot found
    python demo.py start <demo_dir>       start a clean, wait until it runs
    python demo.py wait-docked <demo_dir>
    python demo.py shots <demo_dir>       every screenshot, into <demo_dir>/shots
    python demo.py images <demo_dir> <repo>/docs/images
    python demo.py poke <topic> [json]    a control message for robot.py

Everything in the demo is made up: the robot id, addresses and account
are the project's placeholders, and the history and rooms come from the
anonymised test fixtures.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
#: The fixtures' stores are keyed by this entry id; the demo entry takes it.
ENTRY_ID = "01KRRVYR4T1MPSYM7ACKA5XCBX"
BLID = "0123456789ABCDEF"
HA = "http://127.0.0.1:8123"
ROOMS = ["Kitchen", "Living room", "Hall", "Office", "Bedroom", "Bathroom"]


# ── Home Assistant API ────────────────────────────────────────────────────────

def _token(demo: Path) -> str:
    return str(json.loads((demo / "token.json").read_text())["access_token"])


def _post(path: str, data: dict[str, Any], token: str | None = None, form: bool = False) -> Any:
    if form:
        body = "&".join(f"{k}={v}" for k, v in data.items()).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        body = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(HA + path, body, headers)) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _get(path: str, token: str) -> Any:
    req = urllib.request.Request(HA + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _ws(demo: Path, *commands: dict[str, Any]) -> list[dict[str, Any]]:
    import aiohttp  # noqa: PLC0415 -- installed with Home Assistant

    async def run() -> list[dict[str, Any]]:
        out = []
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(HA + "/api/websocket", max_msg_size=0) as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": _token(demo)})
                await ws.receive_json()
                for n, cmd in enumerate(commands, 1):
                    await ws.send_json({**cmd, "id": n})
                    while True:
                        msg = await ws.receive_json()
                        if msg.get("id") == n and msg.get("type") == "result":
                            out.append(msg)
                            break
        return out

    return asyncio.run(run())


def _state(demo: Path, entity_id: str) -> str:
    return str(_get(f"/api/states/{entity_id}", _token(demo))["state"])


# ── seed ──────────────────────────────────────────────────────────────────────

def seed(demo: Path) -> None:
    """One Classic entry for the simulated robot, with the stored missions,
    coverage grid and rooms of the fixtures, moved in time so the newest
    mission ended yesterday. Run with Home Assistant stopped."""
    sys.path.insert(0, str(REPO))
    from custom_components.roomba_plus.config_flow import (  # noqa: PLC0415
        DEFAULT_OPTIONS,
        RoombaPlusConfigFlow,
    )
    from custom_components.roomba_plus.const import CONF_ENABLE_MAINTENANCE_LIST  # noqa: PLC0415

    storage = demo / "config" / ".storage"
    entries_file = storage / "core.config_entries"
    if entries_file.exists():
        entries = json.loads(entries_file.read_text())
    else:  # nothing set up yet: the store has not been written
        from homeassistant import config_entries as ce  # noqa: PLC0415

        entries = {"version": ce.STORAGE_VERSION, "minor_version": ce.STORAGE_VERSION_MINOR,
                   "key": ce.STORAGE_KEY, "data": {"entries": []}}
    now = dt.datetime.now(dt.UTC).isoformat()
    entries["data"]["entries"] = [
        e for e in entries["data"]["entries"] if e["domain"] != "roomba_plus"
    ] + [{
        "created_at": now, "modified_at": now, "domain": "roomba_plus", "entry_id": ENTRY_ID,
        "data": {"host": "127.0.0.1", "blid": BLID, "password": "demo-password",
                 **DEFAULT_OPTIONS, CONF_ENABLE_MAINTENANCE_LIST: True},
        "options": {CONF_ENABLE_MAINTENANCE_LIST: True},
        "disabled_by": None, "discovery_keys": {}, "pref_disable_new_entities": False,
        "pref_disable_polling": False, "source": "user", "subentries": [], "title": "Roomba",
        "unique_id": BLID, "version": RoombaPlusConfigFlow.VERSION, "minor_version": 1,
    }]
    entries_file.write_text(json.dumps(entries, indent=2))

    missions = json.loads((FIXTURES / f"roomba_plus_missions_02_{ENTRY_ID}.dms").read_text())
    newest = max(
        dt.datetime.fromisoformat(r["ended_at"] or r["started_at"])
        for r in missions["data"]["records"]
    )
    yesterday = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    shift = dt.timedelta(days=(yesterday.date() - newest.date()).days)
    iso = re.compile(r'"(20\d\d-\d\d-\d\dT[0-9:.]+(?:\+00:00|Z)?)"')

    def moved(match: re.Match[str]) -> str:
        try:
            when = dt.datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            return match.group(0)
        return f'"{(when + shift).isoformat()}"'

    names = {f"Room {chr(65 + n)}": name for n, name in enumerate(ROOMS)}
    for kind in ("missions", "grid", "roomseg"):
        text = iso.sub(moved, (FIXTURES / f"roomba_plus_{kind}_02_{ENTRY_ID}.dms").read_text())
        text = re.sub(r'"m_(\d{10})"', lambda m: f'"m_{int(m.group(1)) + int(shift.total_seconds())}"', text)
        for old, new in names.items():
            text = text.replace(f'"{old}"', f'"{new}"')
        (storage / f"roomba_plus_{kind}_{ENTRY_ID}").write_text(text)
    print(f"seeded; history moved by {shift.days} days")


# ── onboard ───────────────────────────────────────────────────────────────────

def onboard(demo: Path) -> None:
    """The first user, onboarding done, and a token for the other steps."""
    client = HA + "/"
    user = _post("/api/onboarding/users", {
        "client_id": client, "name": "Demo User", "username": "demo",
        "password": "demo-demo-demo", "language": "en",
    })
    token = _post("/auth/token", {
        "grant_type": "authorization_code", "code": user["auth_code"], "client_id": client,
    }, form=True)
    for step, data in (
        ("/api/onboarding/core_config", {}),
        ("/api/onboarding/analytics", {}),
        ("/api/onboarding/integration", {"client_id": client, "redirect_uri": client + "?auth_callback=1"}),
    ):
        _post(step, data, token["access_token"])
    (demo / "token.json").write_text(json.dumps(token))
    # A long-lived token, so the steps after the restart need no refresh.
    long_lived = _ws(demo, {"type": "auth/long_lived_access_token",
                            "client_name": "docs demo", "lifespan": 30})[0]["result"]
    (demo / "token.json").write_text(json.dumps({**token, "access_token": long_lived}))
    print("onboarded")


# ── dashboard and rooms ──────────────────────────────────────────────────────

DASHBOARD: dict[str, Any] = {"views": [{"title": "Home", "path": "home", "type": "sections", "max_columns": 2, "sections": [
    {"type": "grid", "cards": [
        {"type": "heading", "heading": "Roomba"},
        {"type": "tile", "entity": "vacuum.roomba", "grid_options": {"columns": 12},
         "features": [{"type": "vacuum-commands", "commands": ["start_pause", "stop", "return_home", "locate"]}]},
        {"type": "picture-entity", "entity": "image.roomba_map", "show_name": False,
         "show_state": False, "grid_options": {"columns": 12}},
    ]},
    {"type": "grid", "cards": [
        {"type": "heading", "heading": "Now"},
        {"type": "tile", "entity": "sensor.roomba_battery", "name": "Battery"},
        {"type": "tile", "entity": "sensor.roomba_phase", "name": "Status"},
        {"type": "tile", "entity": "sensor.roomba_mission_elapsed_time", "name": "Running for"},
        {"type": "tile", "entity": "sensor.roomba_area_cleaned_today", "name": "Cleaned today"},
        {"type": "heading", "heading": "History"},
        {"type": "tile", "entity": "sensor.roomba_last_mission_result", "name": "Last mission"},
        {"type": "tile", "entity": "sensor.roomba_missions_last_30d", "name": "Missions, 30 days"},
        {"type": "tile", "entity": "sensor.roomba_clean_streak", "name": "Clean streak"},
        {"type": "tile", "entity": "sensor.roomba_next_clean", "name": "Next clean"},
        {"type": "heading", "heading": "Maintenance"},
        {"type": "tile", "entity": "sensor.roomba_maintenance", "name": "Maintenance"},
        {"type": "tile", "entity": "sensor.roomba_brush_remaining_hours", "name": "Main brush"},
    ]},
]}]}


def dashboard(demo: Path) -> None:
    results = _ws(
        demo,
        {"type": "lovelace/dashboards/create", "url_path": "robot-demo", "title": "Roomba",
         "icon": "mdi:robot-vacuum", "show_in_sidebar": True, "require_admin": False, "mode": "storage"},
        {"type": "lovelace/config/save", "url_path": "robot-demo", "config": DASHBOARD},
    )
    print("dashboard", [r["success"] for r in results])


def name_rooms(demo: Path) -> None:
    """Configure → Rooms & zones, one name per room the robot found."""
    token = _token(demo)
    flow = _post("/api/config/config_entries/options/flow", {"handler": ENTRY_ID}, token)
    flow = _post(f"/api/config/config_entries/options/flow/{flow['flow_id']}",
                 {"next_step_id": "rooms"}, token)
    fields = [f["name"] for f in flow.get("data_schema") or []]
    if not fields:
        print("rooms: nothing to name")
        return
    names = dict(zip(fields, ROOMS, strict=False))
    _post(f"/api/config/config_entries/options/flow/{flow['flow_id']}", names, token)
    print("rooms named:", names)


# ── the robot ─────────────────────────────────────────────────────────────────

def _wait_for(demo: Path, entity_id: str, state: str, timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if _state(demo, entity_id) == state:
                return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"{entity_id} did not become {state} in {timeout:.0f} s")


def start(demo: Path) -> None:
    _wait_for(demo, "vacuum.roomba", "docked", 300)
    _post("/api/services/vacuum/start", {"entity_id": "vacuum.roomba"}, _token(demo))
    _wait_for(demo, "vacuum.roomba", "cleaning", 60)
    print("cleaning")


def wait_docked(demo: Path) -> None:
    _wait_for(demo, "vacuum.roomba", "docked", 600)
    print("docked")


def poke(topic: str, payload: str = "{}") -> None:
    import aiomqtt  # noqa: PLC0415

    async def send() -> None:
        async with aiomqtt.Client("127.0.0.1", 1883, identifier="demo-poke") as client:
            await client.publish(topic, payload)

    asyncio.run(send())


# ── screenshots ──────────────────────────────────────────────────────────────

#: name: (path, width, height, clicks, hide sidebar). A click is a playwright
#: locator, or "xy:x,y" in CSS pixels -- the gear of the entry has no text.
SHOTS: dict[str, tuple[str, int, int, list[str], bool]] = {
    "dashboard": ("/robot-demo/home", 1200, 780, [], True),
    "device": ("/config/devices/device/{device}", 1300, 1000, [], True),
    "calendar": ("/calendar", 1200, 800, [], True),
    "options_menu": ("/config/integrations/integration/roomba_plus", 1200, 800, ["xy:1010,331"], True),
    "options_zones": ("/config/integrations/integration/roomba_plus", 1200, 1300,
                      ["xy:1010,331", "text=Zone management"], True),
    "options_settings": ("/config/integrations/integration/roomba_plus", 1200, 1300,
                         ["xy:1010,331", "text=Connection settings"], True),
}


def _device_id(demo: Path) -> str:
    devices = _ws(demo, {"type": "config/device_registry/list"})[0]["result"]
    return next(d["id"] for d in devices if any(i[0] == "roomba_plus" for i in d["identifiers"]))


def shots(demo: Path, only: list[str], chrome: str) -> None:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    token = json.loads((demo / "token.json").read_text())
    out = demo / "shots"
    out.mkdir(exist_ok=True)
    device = _device_id(demo)

    async def take() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
            for name, (path, width, height, clicks, hide) in SHOTS.items():
                if only and name not in only:
                    continue
                tokens = {"access_token": token["access_token"], "token_type": "Bearer",
                          "expires_in": 31536000, "hassUrl": HA, "clientId": HA + "/",
                          "expires": 4102444800000, "refresh_token": token.get("refresh_token", "")}
                ctx = await browser.new_context(viewport={"width": width, "height": height},
                                                device_scale_factor=1.5, locale="en-US",
                                                timezone_id="Europe/Berlin")
                await ctx.add_init_script(
                    f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
                    "localStorage.setItem('selectedLanguage', '\"en\"');"
                    f"localStorage.setItem('dockedSidebar', '\"{'always_hidden' if hide else 'docked'}\"');"
                )
                page = await ctx.new_page()
                await page.goto(HA + path.format(device=device))
                await page.wait_for_timeout(6000)
                for click in clicks:
                    if click.startswith("xy:"):
                        x, y = (float(v) for v in click[3:].split(","))
                        await page.mouse.click(x, y)
                    else:
                        await page.locator(click).first.click()
                    await page.wait_for_timeout(2500)
                await page.screenshot(path=str(out / f"{name}.png"))
                await ctx.close()
                print("shot", name)
            await browser.close()

    asyncio.run(take())

    token_str = token["access_token"]
    for entity, name in (("image.roomba_map", "live_map"), ("image.roomba_coverage_map", "coverage_map")):
        if only and name not in only:
            continue
        picture = _get(f"/api/states/{entity}", token_str)["attributes"]["entity_picture"]
        req = urllib.request.Request(HA + picture, headers={"Authorization": "Bearer " + token_str})
        with urllib.request.urlopen(req) as r:
            (out / f"{name}.png").write_bytes(r.read())
        print("image", name)


#: shot: (file in docs/images, crop box in device pixels or None, width)
IMAGES: dict[str, tuple[str, tuple[int, int, int, int] | None, int | None]] = {
    "dashboard": ("dashboard.png", None, 1400),
    "device": ("device-page.png", None, 1300),
    "options_menu": ("options-menu.png", (465, 252, 1335, 948), 870),
    "options_zones": ("zone-management.png", (465, 60, 1335, 1890), 700),
    "options_settings": ("connection-settings.png", (465, 60, 1335, 1890), 700),
    "calendar": ("schedule-calendar.png", None, 1300),
    "live_map": ("live-map.png", None, None),
    "coverage_map": ("coverage-map.png", None, None),
}


def images(demo: Path, target: Path) -> None:
    """Crop, scale and reduce the shots to 256 colours for docs/images."""
    from PIL import Image  # noqa: PLC0415

    for shot, (name, box, width) in IMAGES.items():
        img = Image.open(demo / "shots" / f"{shot}.png").convert("RGB")
        if box:
            img = img.crop(box)
        if width and img.width > width:
            img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.LANCZOS)
        img.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                     dither=Image.Dither.NONE).save(target / name, optimize=True)
        print(name, img.size, (target / name).stat().st_size // 1024, "KB")


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "poke":
        poke(*argv[2:4])
        return
    demo = Path(argv[2]).resolve()
    if cmd == "seed":
        seed(demo)
    elif cmd == "onboard":
        onboard(demo)
    elif cmd == "dashboard":
        dashboard(demo)
    elif cmd == "name-rooms":
        name_rooms(demo)
    elif cmd == "start":
        start(demo)
    elif cmd == "wait-docked":
        wait_docked(demo)
    elif cmd == "shots":
        shots(demo, argv[3:], chrome=__import__("os").environ.get("CHROME", "/usr/bin/google-chrome"))
    elif cmd == "images":
        images(demo, Path(argv[3]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
