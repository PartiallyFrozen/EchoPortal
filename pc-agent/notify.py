"""Send a banner to the EchoPortal shell on the Echo Spot via the running agent.

    python notify.py "Title" "Some text"  [--face comfy] [--ttl 8000] [--silent]

Connects to the agent as a client and asks it to rebroadcast a "notify" message. If config.json
sets an api_token, the token from there (or --token) is sent as a query parameter.
"""
import argparse
import asyncio
import json
import urllib.parse

import websockets

import config

p = argparse.ArgumentParser()
p.add_argument("title")
p.add_argument("text", nargs="?", default="")
p.add_argument("--face", default=None, help="face id to open when the banner is tapped (clock, pcmon, voice, comfy)")
p.add_argument("--ttl", type=int, default=8000)
p.add_argument("--silent", action="store_true")
p.add_argument("--url", default="ws://127.0.0.1:8765/")
p.add_argument("--token", default="", help="shared api_token (defaults to the one in config.json)")
a = p.parse_args()


def hub_url() -> str:
    token = (a.token or config.get("api_token", "") or "").strip()
    if not token:
        return a.url
    sep = "&" if "?" in a.url else "?"
    return a.url + sep + "token=" + urllib.parse.quote(token)


async def main():
    async with websockets.connect(hub_url()) as ws:
        await ws.send(json.dumps({"ch": "notify", "data": {
            "title": a.title, "text": a.text, "face": a.face, "ttl": a.ttl, "chime": not a.silent}}))


asyncio.run(main())
