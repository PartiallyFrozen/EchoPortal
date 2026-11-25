"""Scheduled morning and evening briefings, pushed to connected Spots and the dashboard.

Disabled by default. Times are wall-clock local times computed with the same timezone helper the
timers use, so a DST change does not drift the briefing by an hour.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import re
import time

import config
import timers

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DAY_SECONDS = 86400


def parse_time(value, default: tuple[int, int]) -> tuple[int, int]:
    """Parse 'HH:MM', returning the default for junk values."""
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return default
    hour, minute = int(match.group(1)), int(match.group(2))
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else default


def next_run(now: dt.datetime | None, times: tuple[tuple[int, int], ...]) -> dt.datetime:
    """First future instant for any of the given local wall-clock times."""
    zone = timers._local_tz()
    local = dt.datetime.now(zone) if now is None else now.astimezone(zone)
    candidates = []
    for offset in (0, 1):
        day = local.date() + dt.timedelta(days=offset)
        for hour, minute in times:
            candidates.append(dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone))
    return min(t for t in candidates if t > local)


def _events_today(calendar: dict | None, zone) -> list[str]:
    if not isinstance(calendar, dict):
        return []
    today = dt.datetime.now(zone).date()
    lines = []
    for event in calendar.get("events") or []:
        if not isinstance(event, dict):
            continue
        try:
            start = dt.datetime.fromtimestamp(int(event.get("start", 0)) / 1000, zone)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        if start.date() == today:
            lines.append("%s at %s" % (event.get("title") or "(untitled)", start.strftime("%H:%M")))
    return lines[:5]


def home_summary(home: dict | None) -> str:
    if not isinstance(home, dict) or not home.get("ok", True):
        return ""
    states = home.get("states") or []
    if not isinstance(states, list):
        return ""
    interesting = ("light", "switch", "climate", "lock", "person", "device_tracker")
    parts = []
    for state in states:
        if not isinstance(state, dict):
            continue
        entity = str(state.get("entity_id") or "")
        domain = entity.split(".")[0]
        value = str(state.get("state") or "").lower()
        if domain not in interesting or value in ("unavailable", "unknown", "none"):
            continue
        name = str((state.get("attributes") or {}).get("friendly_name") or entity.split(".")[-1])
        if domain == "person":
            continue
        parts.append("%s %s" % (name, value.replace("_", " ")))
    return "; ".join(parts[:6])


class BriefingScheduler:
    """Periodically composes a short briefing and calls notify/speak callbacks."""

    def __init__(self, notify, speak=None, context_provider=None):
        self.notify = notify
        self.speak = speak
        self.context_provider = context_provider
        self.enabled = bool(config.get("briefing_enabled", False))
        self.morning = parse_time(config.get("briefing_morning_time", ""), (7, 0))
        self.evening = parse_time(config.get("briefing_evening_time", ""), (21, 0))
        self.face = str(config.get("briefing_face", "assistant") or "assistant")
        self.chime = bool(config.get("briefing_chime", True))
        self.speak_enabled = bool(config.get("briefing_speak", True))
        self.ttl = config.as_int("briefing_ttl", 12000, minimum=1000, maximum=60000)
        self._wake = asyncio.Event()
        self.last: dict[str, float] = {}

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "morning": "%02d:%02d" % self.morning,
            "evening": "%02d:%02d" % self.evening,
            "speak": self.speak_enabled,
            "face": self.face,
            "last": dict(self.last),
            "next": next_run(None, (self.morning, self.evening)).isoformat(),
        }

    def compose(self, kind: str, context: dict | None = None) -> str:
        zone = timers._local_tz()
        now = dt.datetime.now(zone)
        greeting = "Good morning, sir." if kind == "morning" else "Good evening, sir."
        lines = [greeting, "It is %s, %s." % (now.strftime("%A %d %B"), now.strftime("%H:%M"))]
        context = context if isinstance(context, dict) else {}
        events = _events_today(context.get("calendar"), zone)
        if events:
            lines.append("On your calendar today: " + "; ".join(events) + ".")
        else:
            lines.append("No events on your calendar today.")
        home = home_summary(context.get("home"))
        if home:
            lines.append("At home: " + home + ".")
        return " ".join(lines)

    def _context(self) -> dict:
        if self.context_provider is None:
            return {}
        try:
            result = self.context_provider()
            return result if isinstance(result, dict) else {}
        except Exception:  # noqa: BLE001 - a briefing must still fire without home data
            return {}

    async def fire(self, kind: str) -> str:
        """Compose and deliver one briefing. Kept separate so tests can call it directly."""
        text = self.compose(kind, self._context())
        title = "Morning briefing" if kind == "morning" else "Evening briefing"
        result = self.notify(title, text, face=self.face, chime=self.chime, ttl=self.ttl)
        if inspect.isawaitable(result):
            await result
        if self.speak_enabled and self.speak is not None:
            try:
                await self.speak(text)
            except Exception as exc:  # noqa: BLE001 - the visual briefing still succeeded
                print("briefing audio skipped: %s" % exc)
        self.last[kind] = time.time()
        return text

    async def run(self):
        zone = timers._local_tz()
        while True:
            due = next_run(None, (self.morning, self.evening))
            seconds = max(1.0, (due - dt.datetime.now(zone)).total_seconds())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=seconds)
                self._wake.clear()
                continue
            except TimeoutError:
                pass
            kind = "morning" if due.hour == self.morning[0] and due.minute == self.morning[1] else "evening"
            await self.fire(kind)
