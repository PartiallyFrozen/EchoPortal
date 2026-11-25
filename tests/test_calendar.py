"""Calendar/agenda: ICS parsing, recurrence, all-day events and local files."""
import asyncio
import datetime as dt

import calendar_ics

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:one
DTSTAMP:20260101T000000Z
DTSTART:20261109T150000Z
DTEND:20261109T160000Z
SUMMARY:Team sync
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:two
DTSTAMP:20260101T000000Z
DTSTART:20261102T120000Z
DTEND:20261102T130000Z
RRULE:FREQ=WEEKLY;COUNT=5
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:three
DTSTAMP:20260101T000000Z
DTSTART;VALUE=DATE:20261110
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""


def test_parses_timed_and_recurring_events():
    now = dt.datetime(2026, 11, 9, 11, 0, tzinfo=dt.timezone.utc)
    events = calendar_ics.parse_ics(ICS, days=2, now=now, source="test")
    titles = [e["title"] for e in events]
    assert "Team sync" in titles
    assert "Standup" in titles, "the weekly event recurs on the same day"
    assert events == sorted(events, key=lambda e: e["start"])
    assert all(e["calendar"] == "test" for e in events)


def test_all_day_events_are_marked():
    now = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.timezone.utc)
    events = calendar_ics.parse_ics(ICS, days=2, now=now)
    holiday = next(e for e in events if e["title"] == "Holiday")
    assert holiday["all_day"] is True
    assert holiday["end"] - holiday["start"] == 86400 * 1000


def test_events_outside_the_window_are_dropped():
    now = dt.datetime(2026, 11, 20, 14, 0, tzinfo=dt.timezone.utc)
    events = calendar_ics.parse_ics(ICS, days=1, now=now)
    assert events == []


def test_a_local_ics_file_is_read(tmp_path):
    path = tmp_path / "calendar.ics"
    path.write_text(ICS, encoding="utf-8")
    assert "BEGIN:VCALENDAR" in calendar_ics._read_url(str(path))


def _ics(*events):
    body = "".join(
        "BEGIN:VEVENT\nUID:%s\nDTSTAMP:20260101T000000Z\nDTSTART:%s\nDTEND:%s\nSUMMARY:%s\nEND:VEVENT\n"
        % (uid, start, end, title)
        for uid, start, end, title in events
    )
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//EN\n" + body + "END:VCALENDAR\n"


def _utc(hours_from_now):
    when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(hours=hours_from_now)
    return when.strftime("%Y%m%dT%H%M%SZ")


def _watcher(monkeypatch, urls, days=7):
    monkeypatch.setattr(calendar_ics.config, "get", lambda name, fallback=None: {
        "calendar_urls": urls, "calendar_days": days, "calendar_refresh_min": 15,
    }.get(name, fallback))
    sent = []

    async def broadcast(data):
        sent.append(data)

    return calendar_ics.CalendarWatcher(broadcast), sent


def test_feeds_are_merged_and_the_same_event_is_not_shown_twice(tmp_path, monkeypatch):
    start, end = _utc(2), _utc(3)
    a = tmp_path / "a.ics"
    b = tmp_path / "b.ics"
    # The same invitation lives in both calendars; each feed also has one unique event.
    a.write_text(_ics(("shared", start, end, "Flight"), ("a", _utc(4), _utc(5), "Dentist")), encoding="utf-8")
    b.write_text(_ics(("shared", start, end, "Flight"), ("b", _utc(6), _utc(7), "Gym")), encoding="utf-8")

    watcher, sent = _watcher(monkeypatch, [str(a), str(b)])
    asyncio.run(watcher.refresh())

    titles = [e["title"] for e in watcher.events]
    assert titles.count("Flight") == 1
    assert sorted(titles) == ["Dentist", "Flight", "Gym"]
    assert watcher.events == sorted(watcher.events, key=lambda e: e["start"])
    assert watcher.error is None
    assert watcher.updated > 0
    assert sent and sent[-1]["configured"] is True


def test_a_busy_window_is_capped(tmp_path, monkeypatch):
    events = [("e%d" % i, _utc(2 + i), _utc(3 + i), "Event %d" % i) for i in range(6)]
    path = tmp_path / "many.ics"
    path.write_text(_ics(*events), encoding="utf-8")

    monkeypatch.setattr(calendar_ics, "MAX_EVENTS", 4)
    watcher, _ = _watcher(monkeypatch, [str(path)])
    asyncio.run(watcher.refresh())

    assert len(watcher.events) == 4
    assert watcher.events == sorted(watcher.events, key=lambda e: e["start"])
