"""Scheduled briefings are opt-in, offline and testable without a running agent."""
import asyncio
import datetime as dt
import time

import briefing
import config
import timers


def test_parse_time_and_next_run_are_local_and_future():
    assert briefing.parse_time("07:05", (7, 0)) == (7, 5)
    assert briefing.parse_time("25:00", (7, 0)) == (7, 0)
    zone = timers._local_tz()
    now = dt.datetime(2026, 11, 19, 8, 0, tzinfo=zone)
    due = briefing.next_run(now, ((7, 0), (21, 0)))
    assert (due.hour, due.minute) == (21, 0)
    assert due > now


def test_compose_includes_calendar_and_home_context():
    zone = timers._local_tz()
    start = int(dt.datetime.now(zone).replace(hour=9, minute=30).timestamp() * 1000)
    context = {
        "calendar": {"events": [{"title": "Dentist", "start": start}]},
        "home": {"ok": True, "states": [
            {"entity_id": "light.living_room", "state": "on",
             "attributes": {"friendly_name": "Living room light"}},
            {"entity_id": "person.justin", "state": "home", "attributes": {}},
            {"entity_id": "sensor.noise", "state": "12", "attributes": {}},
        ]},
    }
    text = briefing.BriefingScheduler(lambda *a, **kw: None).compose("morning", context)
    assert "Good morning" in text
    assert "Dentist at 09:30" in text
    assert "Living room light on" in text
    assert "sensor.noise" not in text


def test_fire_pushes_banner_and_spoken_audio():
    seen = []
    scheduler = briefing.BriefingScheduler(
        lambda *a, **kw: seen.append(("notify", a, kw)),
        lambda text: _speak(seen, text),
        lambda: {"calendar": {}},
    )
    text = asyncio.run(scheduler.fire("evening"))
    assert "Good evening" in text
    assert seen[0][0] == "notify" and seen[0][2]["face"] == scheduler.face
    assert seen[1] == ("audio", text)
    assert "evening" in scheduler.last and scheduler.last["evening"] > 0


async def _speak(seen, text):
    seen.append(("audio", text))


def test_snapshot_reports_disabled_by_default(monkeypatch):
    monkeypatch.setitem(config.VALUES, "briefing_enabled", False)
    snapshot = briefing.BriefingScheduler(lambda *a, **kw: None).snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["morning"] == "07:00" and snapshot["evening"] == "21:00"
    assert snapshot["next"]
    assert time.time() > 0
