"""Send a banner to the EchoPortal shell on the Echo Spot via the running agent.

    python notify.py "Title" "Some text"  [--face comfy] [--ttl 8000] [--silent]

Connects to the agent as a client and asks it to rebroadcast a "notify" message.
"""
import argparse
import asyncio
import json

import websockets

p = argparse.ArgumentParser()
p.add_argument("title")
p.add_argument("text", nargs="?", default="")
p.add_argument("--face", default=None, help="face id to open when the banner is tapped (clock, pcmon, voice, comfy)")
p.add_argument("--ttl", type=int, default=8000)
p.add_argument("--silent", action="store_true")
p.add_argument("--url", default="ws://127.0.0.1:8765")
a = p.parse_args()


async def main():
    async with websockets.connect(a.url) as ws:
        await ws.send(json.dumps({"ch": "notify", "data": {
            "title": a.title, "text": a.text, "face": a.face, "ttl": a.ttl, "chime": not a.silent}}))


asyncio.run(main())
