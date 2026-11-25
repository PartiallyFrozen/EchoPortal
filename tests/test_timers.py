"""Timers, alarms and the wake-up light: parsing, persistence and firing."""
import asyncio
import json
import time

import timers


def test_human_duration():
    assert timers.human_duration(45) == "45 seconds"
    assert timers.human_duration(600) == "10 minutes"
    assert timers.human_duration(3600) == "1 hour"
    assert timers.human_duration(5400) == "1 hour 30 minutes"
    assert timers.human_duration(90) == "1 minute 30 seconds"


def test_parse_duration_accepts_units_and_words():
    assert timers.parse_duration("10 minutes") == 600
    assert timers.parse_duration("1 hour 30 minutes") == 5400
    assert timers.parse_duration("90s") == 90
    assert timers.parse_duration("half an hour") == 1800
    assert timers.parse_duration("an hour") == 3600
    assert timers.parse_duration("no duration here") is None


def test_parse_duration_accepts_spoken_numbers():
    assert timers.parse_duration("two minutes") == 120
    assert timers.parse_duration("ten minutes") == 600
    assert timers.parse_duration("twenty five minutes") == 1500
    assert timers.parse_duration("twenty-five minutes") == 1500
    assert timers.parse_duration("one hour and thirty minutes") == 5400
    assert timers.parse_command("set a timer for two minutes")["seconds"] == 120


def test_local_timezone_uses_the_configured_iana_zone(monkeypatch):
    monkeypatch.setattr(timers.config, "get", lambda name, fallback=None: "America/New_York"
                        if name == "timezone" else fallback)
    zone = timers._local_tz()
    assert getattr(zone, "key", None) == "America/New_York"


def test_an_alarm_keeps_wall_time_across_the_fall_back(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, 0, 30, tzinfo=ny)

    monkeypatch.setattr(timers.config, "get", lambda name, fallback=None: "America/New_York"
                        if name == "timezone" else fallback)
    monkeypatch.setattr(timers, "datetime", FakeDatetime)
    store = timers.TimerStore(path=str(tmp_path / "timers.json"))
    item = store.add_alarm(6, 30)
    assert item["duration_s"] == 7 * 3600, "00:30 EDT to 06:30 EST is seven real hours"


def test_parse_a_timer_command():
    cmd = timers.parse_command("set a timer for 10 minutes")
    assert cmd == {"op": "timer", "seconds": 600, "label": "Timer", "wake": False}


def test_parse_a_reminder_label():
    cmd = timers.parse_command("remind me in 5 minutes to stretch")
    assert cmd["op"] == "timer"
    assert cmd["seconds"] == 300
    assert cmd["label"] == "Stretch"


def test_parse_a_wake_timer():
    cmd = timers.parse_command("wake me in 8 hours")
    assert cmd["op"] == "timer"
    assert cmd["seconds"] == 28800
    assert cmd["wake"] is True


def test_parse_an_alarm():
    cmd = timers.parse_command("wake me at 6:30")
    assert cmd == {"op": "alarm", "hour": 6, "minute": 30, "label": "Wake up", "wake": True}
    assert timers.parse_command("set an alarm for 7 pm")["hour"] == 19
    assert timers.parse_command("alarm at 12 am")["hour"] == 0


def test_parse_a_cancel_command():
    assert timers.parse_command("cancel the timer") == {"op": "cancel"}
    assert timers.parse_command("stop my alarm") == {"op": "cancel"}


def test_ordinary_dictation_is_not_a_command():
    assert timers.parse_command("please write a short poem about rain") is None
    assert timers.parse_command("") is None


def test_store_adds_and_reports_the_remaining_time(tmp_path):
    store = timers.TimerStore(path=str(tmp_path / "timers.json"))
    item = store.add_timer(600, "Tea")
    snap = store.snapshot(now=item["ends_at"] - 300)
    assert snap["items"][0]["label"] == "Tea"
    assert snap["items"][0]["remaining_s"] == 300
    assert snap["items"][0]["state"] == "running"


def test_store_cancels_the_nearest_timer(tmp_path):
    store = timers.TimerStore(path=str(tmp_path / "timers.json"))
    store.add_timer(600, "far")
    near = store.add_timer(60, "near")
    assert store.cancel()["id"] == near["id"]
    assert [i["label"] for i in store.items] == ["far"]


def test_store_persists_across_a_restart(tmp_path):
    path = str(tmp_path / "timers.json")
    store = timers.TimerStore(path=path)
    store.add_timer(600, "Tea")
    again = timers.TimerStore(path=path)
    assert [i["label"] for i in again.items] == ["Tea"]


def test_store_dismisses_finished_timers(tmp_path):
    store = timers.TimerStore(path=str(tmp_path / "timers.json"))
    item = store.add_timer(1, "done")
    item["state"] = "done"
    assert store.dismiss() == 1
    assert store.items == []


def test_a_malformed_store_does_not_take_the_agent_down(tmp_path):
    path = tmp_path / "timers.json"
    path.write_text("{ not json", encoding="utf-8")
    assert timers.TimerStore(path=str(path)).items == []


def test_entries_with_junk_fields_are_dropped_instead_of_crashing(tmp_path):
    path = tmp_path / "timers.json"
    path.write_text(json.dumps({"items": [
        {"id": "bad", "ends_at": "soon"},
        {"id": "good", "ends_at": time.time() + 600, "duration_s": "oops", "label": None},
    ]}), encoding="utf-8")
    store = timers.TimerStore(path=str(path))
    assert [i["id"] for i in store.items] == ["good"]
    assert store.snapshot()["items"][0]["duration_s"] == 0


def test_a_timer_that_expired_while_the_agent_was_off_fires_on_restart(tmp_path):
    path = tmp_path / "timers.json"
    path.write_text(json.dumps({"items": [{
        "id": "abc", "label": "Wake up", "kind": "timer", "ends_at": time.time() - 10,
        "duration_s": 60, "wake": True, "state": "running", "fired": False,
    }]}), encoding="utf-8")
    events = []

    async def broadcast(data):
        events.append(("broadcast", data))

    async def notify(*args):
        events.append(("notify", args))

    store = timers.TimerStore(broadcast, notify, path=str(path))

    async def go():
        try:
            await asyncio.wait_for(store.run(), timeout=1.2)
        except asyncio.TimeoutError:
            pass

    asyncio.run(go())
    assert any(e[0] == "notify" for e in events)
    wake = [e[1] for e in events if e[0] == "broadcast" and isinstance(e[1], dict) and e[1].get("op") == "wake"]
    assert wake, "a restart near an alarm must still start the wake ramp"


def test_run_fires_a_timer_and_starts_the_wake_ramp(tmp_path):
    events = []

    async def broadcast(data):
        events.append(("broadcast", data))

    async def notify(*args):
        events.append(("notify", args))

    store = timers.TimerStore(broadcast, notify, path=str(tmp_path / "timers.json"))
    store.add_timer(0.05, "Wake up", wake=True)

    async def go():
        try:
            await asyncio.wait_for(store.run(), timeout=1.2)
        except asyncio.TimeoutError:
            pass

    asyncio.run(go())
    assert store.items[0]["state"] == "done"
    assert any(e[0] == "notify" for e in events)
    wake = [e[1] for e in events if e[0] == "broadcast" and isinstance(e[1], dict) and e[1].get("op") == "wake"]
    assert wake and wake[0]["minutes"] >= 1
    assert json.dumps(store.snapshot())  # snapshot stays serialisable
