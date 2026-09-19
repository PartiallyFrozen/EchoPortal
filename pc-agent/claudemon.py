"""Claude Code monitor for the EchoPortal "Claude" face.

Listens on UDP 127.0.0.1:8767 for events from claude_hook.py (installed as Claude Code hooks) and keeps a
small state machine: offline / idle / thinking / working / waiting / done. Broadcasts a snapshot on every
event and every 2 s (elapsed turn time), and raises a Spot banner when Claude needs the user.

Broadcast: {"ch":"claude","data":{"up","status","tool","detail","prompt","message","project","model",
            "turn_elapsed","tools_turn","tools_session","subagents","events":[...],"last_event_age"}}
"""
import asyncio
import json
import time
from datetime import datetime

import psutil

PORT = 8767
IDLE_AFTER = 45          # s after "done" with nothing new -> idle
HEARTBEAT = 2.0


class ClaudeMon:
    def __init__(self, broadcast, notify):
        self.broadcast = broadcast          # async (dict) -> None
        self.notify = notify                # async (title, text, face) -> None
        self.up = False
        self.status = "offline"
        self.tool = ""; self.detail = ""; self.prompt = ""; self.message = ""
        self.project = ""; self.model = ""; self.session = ""
        self.turn_started = 0.0; self.turn_elapsed = 0.0
        self.tools_turn = 0; self.tools_session = 0; self.subagents = 0
        self.events = []                    # newest last, "HH:MM:SS  text"
        self.last_event = 0.0
        self._dirty = asyncio.Event()

    # ---- state ------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        if self.status in ("working", "thinking") and self.turn_started:
            self.turn_elapsed = time.time() - self.turn_started
        return {"up": self.up, "status": self.status, "tool": self.tool, "detail": self.detail, "prompt": self.prompt,
                "message": self.message, "project": self.project, "model": self.model, "session": self.session,
                "turn_elapsed": round(self.turn_elapsed, 1), "tools_turn": self.tools_turn, "tools_session": self.tools_session,
                "subagents": self.subagents, "events": self.events[-10:],
                "last_event_age": round(time.time() - self.last_event, 1) if self.last_event else None}

    def _log(self, text):
        self.events.append(datetime.now().strftime("%H:%M:%S") + "  " + text[:56])
        del self.events[:-30]

    def on_event(self, e: dict):
        ev = e.get("event", "")
        self.last_event = time.time()
        self.up = True
        if e.get("project"):
            self.project = e["project"]
        if e.get("model"):
            self.model = e["model"]
        if e.get("session"):
            if e["session"] != self.session:
                self.session = e["session"]; self.tools_session = 0; self.subagents = 0
        banner = None
        if ev == "SessionStart":
            self.status = "idle"; self.tool = ""; self.detail = ""; self.message = ""
            self._log("session started" + (" · " + self.project if self.project else ""))
        elif ev == "UserPromptSubmit":
            self.status = "thinking"; self.prompt = e.get("prompt", ""); self.message = ""
            self.tool = ""; self.detail = ""; self.turn_started = time.time(); self.turn_elapsed = 0.0; self.tools_turn = 0
            self._log("you: " + self.prompt)
        elif ev == "PreToolUse":
            self.status = "working"; self.tool = e.get("tool", ""); self.detail = e.get("detail", ""); self.message = ""
            self.tools_turn += 1; self.tools_session += 1
            if not self.turn_started:
                self.turn_started = time.time()
            self._log(self.tool + (" · " + self.detail if self.detail else ""))
        elif ev == "PostToolUse":
            self.status = "thinking"
            if e.get("ok") is False:
                self._log("  ✗ " + (e.get("tool") or self.tool) + " failed")
        elif ev == "Notification":
            self.status = "waiting"; self.message = e.get("message", "") or "Claude needs you"
            self._log("waiting: " + self.message)
            banner = ("Claude needs you", self.message[:60], True)
        elif ev == "Stop":
            self.status = "done"; self.tool = ""; self.detail = ""
            if self.turn_started:
                self.turn_elapsed = time.time() - self.turn_started
            self.turn_started = 0.0
            self._log("finished · %d tools · %ds" % (self.tools_turn, self.turn_elapsed))
            banner = ("Claude finished", (self.prompt[:50] + "…") if len(self.prompt) > 50 else self.prompt, False)
        elif ev == "SubagentStart":
            self.subagents += 1; self._log("subagent started")
        elif ev == "SubagentStop":
            self.subagents = max(0, self.subagents - 1); self._log("subagent finished")
        elif ev == "SessionEnd":
            self.status = "idle"; self.tool = ""; self.detail = ""; self._log("session ended")
        self._dirty.set()
        return banner

    # ---- tasks ------------------------------------------------------------------------------
    async def run(self):
        loop = asyncio.get_running_loop()
        mon = self

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                try:
                    e = json.loads(data.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    return
                banner = mon.on_event(e)
                if banner:
                    title, text, chime = banner
                    loop.create_task(mon.notify(title, text, "claude", chime))

        await loop.create_datagram_endpoint(Proto, local_addr=("127.0.0.1", PORT))
        print("claudemon: listening on udp", PORT)
        last_proc = 0.0
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=HEARTBEAT)
            except asyncio.TimeoutError:
                pass
            self._dirty.clear()
            now = time.time()
            if now - last_proc > 5:
                last_proc = now
                running = await loop.run_in_executor(None, _claude_running)
                if running != self.up and not (running and self.status != "offline"):
                    self.up = running
                    if not running:
                        self.status = "offline"
                    elif self.status == "offline":
                        self.status = "idle"
            if self.status == "done" and self.last_event and now - self.last_event > IDLE_AFTER:
                self.status = "idle"
            await self.broadcast(self.snapshot())


def _claude_running() -> bool:
    try:
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() in ("claude.exe", "claude"):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False
