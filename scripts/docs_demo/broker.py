"""A local stand-in for a Roomba's own MQTT broker, for the documentation demo.

A Classic robot runs an MQTT broker on TLS port 8883 and Home Assistant
connects to it. Here `amqtt` plays that broker and `robot.py` publishes
into it what a Roomba 980 would. Plain MQTT on 127.0.0.1:1883 is for
`demo.py poke`, which steers the simulated robot.

    python broker.py <cert.pem> <key.pem>
"""
from __future__ import annotations

import asyncio
import logging
import sys

from amqtt.broker import Broker


async def main(cert: str, key: str) -> None:
    broker = Broker({
        "listeners": {
            "default": {"type": "tcp", "bind": "127.0.0.1:1883"},
            "tls": {"type": "tcp", "bind": "127.0.0.1:8883", "ssl": True,
                    "certfile": cert, "keyfile": key},
        },
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    })
    await broker.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
