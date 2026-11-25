"""Timers, alarms and the wake-up light for the Echo Spot.

The PC owns the clock: a timer keeps running if the Spot sleeps, reboots or drops off Wi-Fi. The
shell gets a snapshot on connect and whenever something changes, then counts down locally.

Protocol (channel "timer"):
  PC -> Spot   {"items": [{id,label,kind,ends_at,remaining_s,duration_s,wake,state}], "now": ms}
  PC -> Spot   {"op":"wake","minutes":20}          start a slow brightness ramp on the Spot
  Spot -> PC   {"op":"cancel","id":...}             cancel one timer (omit id = nearest running)
  Spot -> PC   {"op":"dismiss","id":...}            clear a finished timer
  Spot -> PC   {"op":"clear"}                       remove everything

Voice phrases understood (see parse_command):
  "set a timer for 10 minutes" / "timer 90 seconds" / "remind me in 5 minutes to stretch"
  "wake me at 6:30" / "set an alarm for 7 am" / "wake me in 8 hours"
  "cancel the timer" / "stop the alarm"
"""
import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta

import config

STORE = config.data_path("timers.json")

_UNITS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b")
_ALARM_RE = re.compile(r"\b(?:at|for)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")

# Spoken numbers: Gemini often transcribes "two minutes" rather than "2 minutes".
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
_WORD_NUM_RE = re.compile(
    r"\b((?:(?:%s)(?:[ -](?:%s))?)|(?:%s))\s+(?=(?:hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b)"
    % ("|".join(_TENS), "|".join(_ONES), "|".join(_ONES))
)


def _word_value(text: str) -> int:
    total = 0
    for word in re.split(r"[ -]+", text):
        total += _TENS.get(word, _ONES.get(word, 0))
    return total


def _num(value, default=None):
    """float() that tolerates the junk a hand-edited JSON file can contain."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _local_tz():
    """The configured IANA zone, so an alarm across a DST change still lands at the right wall time."""
    name = str(config.get("timezone", "auto") or "auto").strip()
    if name and name.lower() != "auto":
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - unknown zone or missing tzdata: fall back to the PC's zone
            print("timers: unknown timezone %r; using the system timezone" % name)
    return datetime.now().astimezone().tzinfo


def _plural(n: float, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def human_duration(seconds: float) -> str:
    """600 -> '10 minutes', 5400 -> '1 hour 30 minutes'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return _plural(seconds, "second")
    parts = []
    for size, name in ((3600, "hour"), (60, "minute")):
        if seconds >= size:
            n, seconds = divmod(seconds, size)
            parts.append(_plural(n, name))
    if seconds:
        parts.append(_plural(seconds, "second"))
    return " ".join(parts)


def parse_duration(text: str) -> int | None:
    """Seconds from '1 hour 30 minutes', '90s', 'two minutes', 'half an hour'. None when there is no duration."""
    total = 0.0
    found = False
    for m in re.finditer(r"\bhalf an? hour\b", text):
        total += 1800
        found = True
    text = re.sub(r"\bhalf an? hour\b", " ", text)
    # "two minutes" / "twenty-five minutes" -> "2 minutes" / "25 minutes", then the digits path below.
    text = _WORD_NUM_RE.sub(lambda m: str(_word_value(m.group(1))), text)
    for m in re.finditer(r"\b(?:an?|one)\s+(hour|minute|second)\b", text):
        total += _UNITS[m.group(1)]
        found = True
    text = re.sub(r"\b(?:an?|one)\s+(?:hour|minute|second)\b", " ", text)
    for m in _DUR_RE.finditer(text):
        total += float(m.group(1)) * _UNITS[m.group(2)]
        found = True
    return int(round(total)) if found and total > 0 else None


def parse_command(text: str) -> dict | None:
    """Recognise a timer/alarm command in a voice transcript. None means 'just dictation'."""
    t = " ".join(text.lower().strip().split())
    if not t:
        return None

    # Cancel: only when the sentence actually mentions a timer or alarm.
    if re.search(r"\b(cancel|stop|clear|delete|remove)\b", t) and re.search(r"\b(timer|timers|alarm|alarms|countdown)\b", t):
        return {"op": "cancel"}

    wake = bool(re.search(r"\bwake me\b", t))
    if re.search(r"\b(timer|countdown)\b", t) or re.search(r"\b(remind me|wake me)(?: up)? in\b", t):
        seconds = parse_duration(t)
        if seconds:
            label = "Wake up" if wake else "Timer"
            m = re.search(r"\bremind me in .*?\bto\s+(.+)$", t)
            if m:
                label = m.group(1).strip().capitalize()
            return {"op": "timer", "seconds": seconds, "label": label, "wake": wake}

    if re.search(r"\b(alarm|wake me)\b", t):
        m = _ALARM_RE.search(t)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = (m.group(3) or "").lower()
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm and 1 <= hour <= 11:
                # "wake me at 6:30" almost always means the morning.
                pass
            return {"op": "alarm", "hour": hour, "minute": minute, "label": "Wake up", "wake": True}
    return None


class TimerStore:
    """Timers and alarms, persisted to timers.json so an agent restart does not lose them."""

    def __init__(self, broadcast=None, notify=None, path: str | None = None):
        self.broadcast = broadcast
        self.notify = notify
        self.path = path or STORE
        self.items: list[dict] = []
        self._load()

    # ---- persistence -------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
            items = data.get("items", data) if isinstance(data, dict) else data
            if isinstance(items, list):
                clean = []
                for i in items:
                    if not isinstance(i, dict) or not i.get("id"):
                        continue
                    ends = _num(i.get("ends_at"))
                    if ends is None:          # a hand-edited or truncated entry is dropped, not fatal
                        continue
                    i["ends_at"] = ends
                    i["duration_s"] = int(_num(i.get("duration_s"), 0) or 0)
                    i["label"] = str(i.get("label") or "Timer")[:48]
                    clean.append(i)
                self.items = clean
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
            self.items = []
        now = time.time()
        kept = []
        for i in self.items:
            if i.get("state") == "running" and i["ends_at"] <= now:
                # Fired while the agent was off. Show it if it was recent, otherwise forget it.
                if now - i["ends_at"] > 300:
                    continue
                i["state"] = "done"
                # Not fired yet: run() picks it up on its first tick, so a restart near an alarm
                # time does not silently swallow the wake-up.
                i["fired"] = False
            kept.append(i)
        self.items = kept

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": self.items}, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            print("timers: could not save:", e)

    # ---- queries -----------------------------------------------------------------------
    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        items = []
        for i in self.items:
            ends = _num(i.get("ends_at"), 0) or 0
            items.append({
                "id": i["id"], "label": i.get("label", "Timer"), "kind": i.get("kind", "timer"),
                "ends_at": int(ends * 1000),
                "remaining_s": max(0, int(ends - now)),
                "duration_s": int(_num(i.get("duration_s"), 0) or 0),
                "wake": bool(i.get("wake")), "state": i.get("state", "running"),
            })
        items.sort(key=lambda x: (x["state"] != "running", x["ends_at"]))
        return {"items": items, "now": int(now * 1000)}

    def _running(self) -> list[dict]:
        return sorted((i for i in self.items if i.get("state") == "running"),
                      key=lambda i: _num(i.get("ends_at"), 0) or 0)

    # ---- mutations ---------------------------------------------------------------------
    def add_timer(self, seconds: int, label: str = "Timer", wake: bool = False) -> dict:
        seconds = max(1, int(seconds))
        now = time.time()
        item = {
            "id": uuid.uuid4().hex[:10], "label": (label or "Timer").strip()[:48] or "Timer",
            "kind": "timer", "ends_at": now + seconds, "duration_s": seconds,
            "wake": bool(wake), "state": "running", "fired": False,
        }
        self.items.append(item)
        self._save()
        return item

    def add_alarm(self, hour: int, minute: int, label: str = "Alarm", wake: bool = True) -> dict:
        # Zone-aware so an alarm set before a DST change still fires at the right wall time.
        # Subtract timestamps, not datetimes: two aware datetimes sharing a tzinfo are compared as
        # wall-clock values, which would turn the 25-hour fall-back day into 24 hours.
        tz = _local_tz()
        now = datetime.now(tz)
        target = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        seconds = int(target.timestamp() - now.timestamp())
        return self.add_timer(max(1, seconds), label or "Alarm", wake)

    def cancel(self, tid: str | None = None) -> dict | None:
        if tid:
            item = next((i for i in self.items if i["id"] == tid), None)
        else:
            running = self._running()
            item = running[0] if running else None
        if item is not None:
            self.items.remove(item)
            self._save()
        return item

    def dismiss(self, tid: str | None = None) -> int:
        before = len(self.items)
        if tid:
            self.items = [i for i in self.items if not (i["id"] == tid and i.get("state") == "done")]
        else:
            self.items = [i for i in self.items if i.get("state") != "done"]
        if len(self.items) != before:
            self._save()
        return before - len(self.items)

    def clear(self) -> int:
        n = len(self.items)
        self.items = []
        if n:
            self._save()
        return n

    async def _changed(self):
        if self.broadcast:
            await self.broadcast(self.snapshot())

    async def changed(self):
        """Push a fresh snapshot to every connected shell."""
        await self._changed()

    # ---- events ------------------------------------------------------------------------
    async def handle_message(self, data: dict) -> dict:
        op = data.get("op")
        if op == "cancel":
            item = self.cancel(data.get("id"))
            await self._changed()
            return {"text": ("cancelled %s" % item.get("label", "timer")) if item else "no timer to cancel"}
        if op == "dismiss":
            n = self.dismiss(data.get("id"))
            await self._changed()
            return {"text": "%d dismissed" % n}
        if op == "clear":
            n = self.clear()
            await self._changed()
            return {"text": "%d cleared" % n}
        if op in ("add", "timer"):
            try:
                seconds = int(float(data.get("seconds") or 0))
                if not seconds and data.get("minutes"):
                    seconds = int(float(data["minutes"]) * 60)
                if not seconds and data.get("hours"):
                    seconds = int(float(data["hours"]) * 3600)
            except (TypeError, ValueError):
                return {"text": "invalid duration"}
            if not seconds:
                return {"text": "no duration"}
            item = self.add_timer(seconds, data.get("label") or "Timer", bool(data.get("wake")))
            await self._changed()
            return {"text": "timer set for %s" % human_duration(seconds), "item": item}
        return {"text": "unknown timer op"}

    async def handle_text(self, text: str) -> dict | None:
        """Apply a spoken timer/alarm command. None means the text is ordinary dictation."""
        cmd = parse_command(text)
        if not cmd:
            return None
        if cmd["op"] == "timer":
            item = self.add_timer(cmd["seconds"], cmd.get("label") or "Timer", cmd.get("wake", False))
            await self._changed()
            return {"text": "timer set for %s" % human_duration(cmd["seconds"]), "item": item}
        if cmd["op"] == "alarm":
            item = self.add_alarm(cmd["hour"], cmd["minute"], cmd.get("label") or "Alarm", cmd.get("wake", True))
            await self._changed()
            return {"text": "alarm set for %d:%02d" % (cmd["hour"], cmd["minute"]), "item": item}
        if cmd["op"] == "cancel":
            item = self.cancel(None)
            await self._changed()
            return {"text": ("cancelled %s" % item.get("label", "timer")) if item else "no timer to cancel"}
        return None

    async def run(self):
        """Fire due timers. Snapshots are pushed only on change; the Spot counts down itself."""
        while True:
            now = time.time()
            fired = []
            for item in list(self.items):
                ends = _num(item.get("ends_at"))
                if ends is None or ends > now:
                    continue
                if item.get("state") == "running":
                    item["state"] = "done"
                    item["fired"] = True
                    fired.append(item)
                elif item.get("state") == "done" and item.get("fired") is False:
                    # Recovered from a restart: fire once now instead of dropping the alarm.
                    item["fired"] = True
                    fired.append(item)
            if fired:
                self._save()
                try:
                    await self._changed()
                except Exception as e:  # noqa: BLE001
                    print("timers: could not broadcast the snapshot:", repr(e))
                for item in fired:
                    try:
                        label = item.get("label") or ("Wake up" if item.get("wake") else "Timer")
                        if item.get("wake") and self.broadcast:
                            minutes = int(_num(config.get("wake_ramp_minutes", 20), 20) or 20)
                            await self.broadcast({"op": "wake", "minutes": minutes, "text": label})
                        if self.notify:
                            await self.notify("Wake up" if item.get("wake") else "Timer", label, "timer", True)
                    except Exception as e:  # noqa: BLE001 - one bad notification must not stop the clock
                        print("timers: could not fire %r:" % item.get("label"), repr(e))
            await asyncio.sleep(0.5)
