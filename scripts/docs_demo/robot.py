"""A simulated Roomba 980 for the documentation demo.

Publishes a 980's reported state to the local broker (broker.py) the way
the robot does, answers `cmd` and `delta` messages, drives a cleaning path
over the floor of the test fixtures' coverage grid when started, and
answers the setup's UDP discovery probe. Everything is made up: the robot
id, addresses and MAC are the project's placeholders.

    DEMO_GRID=<grid store .dms> python robot.py

DEMO_STEP sets the seconds between two positions (default 0.25).
Control messages on plain MQTT (demo.py poke): `demo/full` re-sends the
whole state, `demo/set` publishes a part of it.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import ssl
import time

import aiomqtt

BLID = "0123456789ABCDEF"
TOPIC = f"aws/things/{BLID}/shadow/update"  # amqtt refuses topics with a leading $
GRID = os.environ.get("DEMO_GRID")

def now() -> int:
    return int(time.time())

STATE = {
    "name": "Roomba", "sku": "R980040", "softwareVer": "v2.4.17-138",
    "hardwareRev": 3, "bootloaderVer": "4042", "umiVer": "6", "navSwVer": "01.12.01#1",
    "wifiSwVer": "20992", "mobilityVer": "5865", "soundVer": "33", "uiSwVer": "4582",
    "mac": "aa:bb:cc:dd:ee:ff", "country": "DE", "cloudEnv": "prod", "timezone": "Europe/Berlin",
    "language": 2, "localtimeoffset": 60, "utctime": now(),
    "netinfo": {"dhcp": True, "addr": 3221225994, "mask": 4294967040, "gw": 3221225985,
                "dns1": 3221225985, "dns2": 0, "bssid": "aa:bb:cc:dd:ee:ff", "sec": 4},
    "wifistat": {"wifi": 1, "uap": False, "cloud": 4},
    "signal": {"rssi": -52, "snr": 38},
    "batPct": 100, "batteryType": "lith",
    "batInfo": {"mDate": "2023-3-14", "mName": "F12432832R", "mDaySerial": 7531, "mData": "303030333034303200000000000000000000000000", "mLife": "0C9D0A6F14D70E4C0F2D00A1FB06001E4F06", "cCount": 388, "afCount": 0},
    "batAuthEnable": True,
    "bin": {"present": True, "full": False},
    "cleanMissionStatus": {"cycle": "none", "phase": "charge", "expireM": 0, "rechrgM": 0,
                           "error": 0, "notReady": 0, "mssnM": 0, "sqft": 0, "initiator": "", "nMssn": 412},
    "pose": {"theta": 0, "point": {"x": 0, "y": 0}},
    "bbrun": {"hr": 312, "min": 48, "sqft": 1873, "nStuck": 31, "nScrubs": 224, "nPicks": 2049,
              "nPanics": 96, "nCliffsF": 3512, "nCliffsR": 187, "nMBStll": 0, "nWStll": 2, "nCBump": 0},
    "bbmssn": {"nMssn": 412, "nMssnOk": 341, "nMssnC": 61, "nMssnF": 10, "aMssnM": 52, "aCycleM": 50},
    "bbchg3": {"avgMin": 110, "hOnDock": 4021, "nAvail": 680, "estCap": 12311,
               "nLithChrg": 388, "nNimhChrg": 0, "nDocks": 423},
    "bbchg": {"nChgOk": 380, "nLithF": 0, "aborts": [0, 0, 0]},
    "bbnav": {"aMtrack": 72, "nGoodLmrks": 6, "aGain": 4, "aExpo": 9},
    "bbrstinfo": {"nNavRst": 7, "nMobRst": 0, "causes": "0000"},
    "bbswitch": {"nBumper": 52310, "nClean": 412, "nSpot": 3, "nDock": 18, "nDrops": 121},
    "bbsys": {"hr": 9120, "min": 31},
    "bbpanic": {"panics": [6, 8, 9, 6, 9]},
    "bbpause": {"pauses": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]},
    "cap": {"pose": 1, "ota": 2, "multiPass": 2, "carpetBoost": 1, "pp": 1, "binFullDetect": 1,
            "langOta": 1, "maps": 1, "edge": 1, "eco": 1, "svcConf": 1},
    "carpetBoost": True, "vacHigh": False, "openOnly": False, "noAutoPasses": False,
    "twoPass": False, "binPause": True, "noPP": False, "ecoCharge": False, "schedHold": False,
    "cleanSchedule": {"cycle": ["none", "start", "none", "start", "none", "start", "none"],
                      "h": [9, 10, 9, 10, 9, 10, 9], "m": [0, 30, 0, 30, 0, 30, 0]},
    "dock": {"known": False},
    "audio": {"active": False},
    "lastCommand": {"command": "dock", "time": now() - 86400, "initiator": "localApp"},
    "langs": [{"en-US": 0}, {"de-DE": 2}],
    "tz": {"events": [{"dt": 0, "off": 60}], "ver": 8},
    "svcEndpoints": {"svcDeplId": "v011"},
    "mapUploadAllowed": True,
}

def cells():
    if not GRID:
        return []
    data = json.load(open(GRID))["data"]["cells"]
    out = []
    for key, v in data.items():
        if v >= 0.5:
            x, y = map(int, key.split(","))
            out.append((x, y))
    return out

def plan_path():
    """A boustrophedon path over the known floor, in cm from the dock."""
    pts = cells()
    if not pts:
        return [(i * 10, (i % 20) * 15) for i in range(200)]
    rows = {}
    for x, y in pts:
        rows.setdefault(y, []).append(x)
    path = []
    # The grid is in the maps' frame, which swaps the firmware's x and y
    # (the convention the integration documents), so the pose swaps back.
    for n, y in enumerate(sorted(rows)[::2]):
        xs = sorted(rows[y])
        if n % 2:
            xs.reverse()
        for x in xs[::2]:
            path.append((y * 15 + 7, x * 15 + 7) if os.environ.get("DEMO_SWAP", "1") == "1" else (x * 15 + 7, y * 15 + 7))
    return path

class Robot:
    def __init__(self, client):
        self.client = client
        self.task = None

    async def publish(self, part: dict):
        STATE.update(part)
        STATE["utctime"] = now()
        await self.client.publish(TOPIC, json.dumps({"state": {"reported": part}}))

    async def full(self):
        # The robot sends its state in several messages after a connect.
        items = list(STATE.items())
        for i in range(0, len(items), 8):
            await self.publish(dict(items[i:i + 8]))
            await asyncio.sleep(0.05)

    def cms(self, **kw):
        cms = dict(STATE["cleanMissionStatus"]); cms.update(kw)
        return {"cleanMissionStatus": cms}

    async def mission(self, initiator: str):
        n = STATE["cleanMissionStatus"]["nMssn"] + 1
        start = now()
        await self.publish(self.cms(cycle="clean", phase="run", initiator=initiator, nMssn=n,
                                    mssnM=0, sqft=0, mssnStrtTm=start, expireTm=start + 7200,
                                    rechrgTm=0, error=0))
        await self.publish({"lastCommand": {"command": "start", "time": start, "initiator": initiator}})
        path = plan_path()
        bat = STATE["batPct"]
        for i, (x, y) in enumerate(path):
            theta = 0 if i == 0 else int(math.degrees(math.atan2(y - path[i - 1][1], x - path[i - 1][0])))
            await self.publish({"pose": {"theta": theta, "point": {"x": x, "y": y}}})
            if i % 25 == 0:
                bat = max(15, bat - 1)
                mins = (now() - start) // 60
                await self.publish({"batPct": bat})
                await self.publish(self.cms(mssnM=mins, sqft=int(i * 0.9)))
            await asyncio.sleep(float(os.environ.get("DEMO_STEP", "0.25")))
            if STATE["cleanMissionStatus"]["phase"] != "run":
                return
        await self.dock("charge")

    async def dock(self, final="charge"):
        await self.publish(self.cms(phase="hmPostMsn"))
        await asyncio.sleep(3)
        await self.publish({"pose": {"theta": 0, "point": {"x": 0, "y": 0}}})
        await self.publish(self.cms(cycle="none", phase=final, mssnM=0))

    async def on_cmd(self, msg: dict):
        cmd = msg.get("command")
        ini = msg.get("initiator", "localApp")
        await self.publish({"lastCommand": {"command": cmd, "time": now(), "initiator": ini}})
        if cmd in ("start", "clean") and STATE["cleanMissionStatus"]["phase"] != "run":
            self.task = asyncio.create_task(self.mission(ini))
        elif cmd == "pause":
            await self.publish(self.cms(phase="stop"))
        elif cmd == "resume":
            await self.publish(self.cms(phase="run"))
        elif cmd == "stop":
            await self.publish(self.cms(cycle="none", phase="stop"))
        elif cmd == "dock":
            await self.publish(self.cms(phase="hmUsrDock"))
            await asyncio.sleep(4)
            await self.dock()
        elif cmd == "evac":
            pass

class Discovery(asyncio.DatagramProtocol):
    """Answers the app's `irobotmcs` probe on UDP 5678, as the robot does."""
    def connection_made(self, transport):
        self.transport = transport
    def datagram_received(self, data, addr):
        if data.strip() == b"irobotmcs":
            reply = {"ver": "3", "hostname": "Roomba-" + BLID, "robotname": STATE["name"],
                     "ip": "127.0.0.1", "mac": STATE["mac"], "sw": STATE["softwareVer"],
                     "sku": STATE["sku"], "nc": 0, "proto": "mqtt", "cap": STATE["cap"]}
            self.transport.sendto(json.dumps(reply).encode(), addr)

async def main():
    loop = asyncio.get_running_loop()
    if os.environ.get("DEMO_DISCOVERY") == "1":
        # Off by default: Home Assistant's own discovery binds the same
        # port, and both cannot run on one host.
        await loop.create_datagram_endpoint(Discovery, local_addr=("127.0.0.1", 5678))
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    while True:
        try:
            async with aiomqtt.Client("127.0.0.1", 8883, username="robot-" + BLID, password="x",
                                      tls_context=ctx, identifier="sim-robot") as client:
                robot = Robot(client)
                await client.subscribe("cmd")
                await client.subscribe("delta")
                await client.subscribe("demo/#")
                await robot.full()

                async def heartbeat():
                    # A new subscriber (Home Assistant connecting or reconnecting)
                    # needs the whole state; the real robot sends it on connect.
                    n = 0
                    while True:
                        await asyncio.sleep(8)
                        n += 1
                        if STATE["cleanMissionStatus"]["phase"] != "run":
                            await robot.full()
                        if n % 8 == 0:
                            await robot.publish({"signal": {"rssi": random.randint(-56, -48), "snr": 38}})
                hb = asyncio.create_task(heartbeat())
                async for message in client.messages:
                    t = str(message.topic)
                    try:
                        body = json.loads(message.payload or b"{}")
                    except ValueError:
                        continue
                    if t == "cmd":
                        await robot.on_cmd(body)
                    elif t == "delta":
                        await robot.publish(body.get("state", {}))
                    elif t == "demo/full":
                        await robot.full()
                    elif t == "demo/set":
                        await robot.publish(body)
                hb.cancel()
        except aiomqtt.MqttError:
            await asyncio.sleep(2)

asyncio.run(main())
