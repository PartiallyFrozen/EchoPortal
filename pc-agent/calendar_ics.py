"""Calendar / agenda channel: subscribe to secret ICS URLs and show the next events on the Spot.

config.json:
  "calendar_urls": ["https://calendar.google.com/calendar/ical/.../basic.ics"],
  "calendar_days": 7,
  "calendar_refresh_min": 15

Google Calendar: Settings > your calendar > "Secret address in iCal format". Outlook and most
others expose a similar read-only ICS link. webcal:// links are accepted too.

Protocol (channel "calendar"):
  PC -> Spot   {"events": [{start,end,all_day,title,location,calendar}], "updated": ms,
                "error": null, "configured": true}
"""
import asyncio
import datetime as dt
import os
import urllib.error
import urllib.parse
import urllib.request

import config

# Bound the snapshot: the face draws at most 8 rows and the whole list is broadcast to every
# client on each refresh, so a very busy week across many calendars should not bloat frames.
MAX_EVENTS = 120


def _as_local(value, tz):
    """icalendar gives dates, naive datetimes, or aware datetimes. Normalise to aware local time."""
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=tz)
    return None


def parse_ics(text: str, days: int = 7, now: dt.datetime | None = None, source: str = "") -> list[dict]:
    """Return the events overlapping [now - 1 h, now + days], sorted by start.

    Imports the calendar libraries lazily so a source install without them still runs the rest of
    the agent; the calendar face simply reports that the dependencies are missing.
    """
    from icalendar import Calendar
    import recurring_ical_events

    now = now or dt.datetime.now().astimezone()
    tz = now.tzinfo
    start = now - dt.timedelta(hours=1)
    end = now + dt.timedelta(days=max(1, int(days)))
    cal = Calendar.from_ical(text)
    name = source or str(cal.get("X-WR-CALNAME") or "")
    out = []
    for e in recurring_ical_events.of(cal).between(start, end):
        raw_start = e.get("dtstart")
        if raw_start is None:
            continue
        all_day = isinstance(raw_start.dt, dt.date) and not isinstance(raw_start.dt, dt.datetime)
        begins = _as_local(raw_start.dt, tz)
        if begins is None:
            continue
        raw_end = e.get("dtend")
        if raw_end is not None:
            ends = _as_local(raw_end.dt, tz)
        else:
            dur = e.get("duration")
            if dur is not None:
                ends = begins + dur.dt
            else:
                ends = begins + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
        if ends is None:
            continue
        if ends < start:
            continue
        out.append({
            "start": int(begins.timestamp() * 1000),
            "end": int(ends.timestamp() * 1000),
            "all_day": bool(all_day),
            "title": str(e.get("summary") or "(untitled)"),
            "location": str(e.get("location") or ""),
            "calendar": name,
        })
    out.sort(key=lambda x: (x["start"], x["title"]))
    seen = set()
    unique = []
    for e in out:
        key = (e["start"], e["title"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _read_url(url: str, timeout: float = 20.0) -> str:
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if url.startswith("file://"):
        # urlparse().path is "/C:/Users/..." on Windows; url2pathname turns it back into a real path.
        path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    if os.path.exists(url):
        with open(url, encoding="utf-8-sig") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "EchoPortal/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


class CalendarWatcher:
    def __init__(self, broadcast):
        self.broadcast = broadcast
        urls = config.get("calendar_urls", [])
        if isinstance(urls, str):
            urls = [urls]
        self.urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
        self.days = config.as_int("calendar_days", 7, minimum=1)
        self.refresh_min = config.as_int("calendar_refresh_min", 15, minimum=1)
        self.events: list[dict] = []
        self.updated = 0
        self.error: str | None = None
        self._force = asyncio.Event()

    def snapshot(self) -> dict:
        return {
            "events": self.events, "updated": self.updated, "error": self.error,
            "configured": bool(self.urls),
        }

    def request_refresh(self) -> bool:
        """Wake the refresh loop. False means there is nothing to refresh (no feeds configured)."""
        if not self.urls:
            return False
        self._force.set()
        return True

    async def refresh(self):
        if not self.urls:
            self.error = "no calendar_urls configured"
            return
        loop = asyncio.get_running_loop()
        events = []
        errors = []
        for url in self.urls:
            label = urllib.parse.urlparse(url).netloc or "calendar"
            try:
                text = await loop.run_in_executor(None, _read_url, url)
                found = await loop.run_in_executor(None, parse_ics, text, self.days, None, label)
                events.extend(found)
                print("calendar: %s -> %d event(s)" % (label, len(found)))
            except Exception as e:  # noqa: BLE001
                # str(HTTPError) includes the full secret URL. Keep only a safe status label so
                # agent.log and the dashboard cannot leak a rotated calendar address.
                code = getattr(e, "code", None)
                if code is not None:
                    detail = "HTTP %s" % code
                elif isinstance(e, urllib.error.URLError):
                    detail = "network error"
                else:
                    detail = type(e).__name__
                message = "%s: %s" % (label, detail)
                errors.append(message)
                print("calendar: fetch failed:", message)
        if events or not errors:
            # Deduplicate across feeds too: an invitation shows up in every calendar that was
            # invited, and imported calendars often carry the same event twice.
            merged = []
            seen = set()
            for e in sorted(events, key=lambda x: (x["start"], x["title"])):
                key = (e["start"], e["title"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(e)
            if len(merged) > MAX_EVENTS:
                print("calendar: showing the first %d of %d events" % (MAX_EVENTS, len(merged)))
            self.events = merged[:MAX_EVENTS]
            self.updated = int(dt.datetime.now().timestamp() * 1000)
        self.error = "; ".join(errors)[:200] if errors else None
        if self.broadcast:
            await self.broadcast(self.snapshot())
        print("calendar: %d feed(s), %d upcoming event(s)%s" % (
            len(self.urls), len(self.events), (" - " + self.error) if self.error else ""))

    async def run(self):
        if not self.urls:
            print("calendar: no calendar_urls configured")
            return
        while True:
            try:
                await self.refresh()
            except Exception as e:  # noqa: BLE001
                print("calendar error:", e)
            try:
                await asyncio.wait_for(self._force.wait(), timeout=self.refresh_min * 60)
            except asyncio.TimeoutError:
                pass
            self._force.clear()
