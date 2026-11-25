"""Build Jarvis-readable Buffalo Sabres 2026-27 schedule files.

Reads the NHL's public club-schedule API and combines it with the Sabres'
published alternate-jersey dates. Writes three local files into the configured
knowledge directory:

  Buffalo-Sabres-2026-27-Schedule.md   human-readable, optimized for search_notes
  Buffalo-Sabres-2026-27-Schedule.json structured data
  calendars/Buffalo-Sabres-2026-27.ics local ICS feed for the calendar channel

This script is a build helper only; it is not part of the packaged agent.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import unicodedata
import urllib.request
import zoneinfo
from pathlib import Path

API_URL = "https://api-web.nhle.com/v1/club-schedule-season/BUF/20262027"
SOURCES = [
    {
        "name": "NHL club schedule API (times, venues, teams, TV, scores)",
        "url": API_URL,
    },
    {
        "name": "Buffalo Sabres official alternate jersey schedule",
        "url": "https://www.nhl.com/sabres/news/2026-27-buffalo-sabres-alternate-jersey-schedule",
    },
    {
        "name": "ColorWay Sports game-by-game uniform log",
        "url": "https://www.colorwaysports.com/stories/sabres-uniform-schedule-2026-27",
    },
    {
        "name": "WBUF alternate jersey schedule recap",
        "url": "https://wbuf.com/buffalo-sabres-alternative-jerseys/",
    },
]

ET = zoneinfo.ZoneInfo("America/New_York")
BLACK_RED = "Black and Red Goathead"
REMIX = "Hometown Remix"
HOME = "Home Royal Blue"
AWAY = "Road White"

# Official Sabres announcement: 10 black/red dates and 8 Hometown Remix dates.
# The article's text omits the Oct. 10 and Nov. 4 rows from its embedded image;
# ColorWay Sports and WBUF list them, bringing each total to the announced count.
JERSEY_OVERRIDES: dict[str, tuple[str, str]] = {
    "2026-10-10": (BLACK_RED, "Utah"),
    "2026-11-08": (BLACK_RED, "Vegas"),
    "2026-11-27": (BLACK_RED, "Pittsburgh"),
    "2026-12-26": (BLACK_RED, "New Jersey"),
    "2027-01-14": (BLACK_RED, "Colorado"),
    "2027-01-21": (BLACK_RED, "Seattle"),
    "2027-02-18": (BLACK_RED, "Carolina"),
    "2027-03-15": (BLACK_RED, "San Jose"),
    "2027-03-25": (BLACK_RED, "Toronto"),
    "2027-04-10": (BLACK_RED, "Detroit"),
    "2026-11-04": (REMIX, "Ottawa"),
    "2026-11-25": (REMIX, "New York Rangers"),
    "2026-12-18": (REMIX, "Montreal"),
    "2026-12-28": (REMIX, "Detroit"),
    "2027-01-16": (REMIX, "Nashville"),
    "2027-01-20": (REMIX, "New York Islanders"),
    "2027-02-26": (REMIX, "Ottawa"),
    "2027-03-04": (REMIX, "St. Louis"),
}

TYPE_LABELS = {1: "Preseason", 2: "Regular Season", 3: "Playoffs"}


def _name(team: dict) -> str:
    return f"{team['placeName']['default']} {team['commonName']['default']}"


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def _start_parts(raw: str) -> tuple[dt.datetime, str]:
    moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    clock = moment.strftime("%I:%M %p %Z").lstrip("0")
    return moment, clock


def _tv(game: dict) -> list[str]:
    seen: list[str] = []
    for item in game.get("tvBroadcasts") or []:
        network = str(item.get("network") or "").strip()
        if network and network not in seen:
            seen.append(network)
    return seen


def _result(game: dict) -> str:
    if game.get("gameState") != "FINAL":
        return ""
    away, home = game["awayTeam"], game["homeTeam"]
    if away.get("score") is None or home.get("score") is None:
        return ""
    return f"{away['abbrev']} {away['score']} at {home['abbrev']} {home['score']}"


def _game_center(game: dict) -> str:
    link = str(game.get("gameCenterLink") or "").strip()
    return "https://www.nhl.com" + link if link.startswith("/") else link


def build_games(raw: dict) -> list[dict]:
    games: list[dict] = []
    for game in raw.get("games") or []:
        game_id = int(game["id"])
        game_type = int(game.get("gameType") or 0)
        date = str(game.get("gameDate") or "")
        away, home = game["awayTeam"], game["homeTeam"]
        is_home = int(home.get("id") or 0) == 7  # NHL team id 7 is Buffalo.
        opponent = away if is_home else home
        moment, clock = _start_parts(game["startTimeUTC"])
        override = JERSEY_OVERRIDES.get(date)
        jersey, note = override if override else ((HOME if is_home else AWAY), "")
        if override and _plain(override[1].split()[0]) not in _plain(_name(opponent)):
            raise RuntimeError(f"jersey date {date} expected {override[1]}, got {_name(opponent)}")
        games.append(
            {
                "id": game_id,
                "date": date,
                "day": moment.strftime("%A"),
                "game_type": TYPE_LABELS.get(game_type, str(game_type)),
                "game_state": str(game.get("gameState") or ""),
                "start_utc": str(game.get("startTimeUTC") or ""),
                "start_et": clock,
                "start_et_iso": moment.isoformat(),
                "team": "Buffalo Sabres",
                "opponent": _name(opponent),
                "opponent_abbrev": str(opponent.get("abbrev") or ""),
                "site": "Home" if is_home else "Away",
                "venue": str((game.get("venue") or {}).get("default") or ""),
                "venue_timezone": str(game.get("venueTimezone") or ""),
                "jersey": jersey,
                "jersey_note": note or ("default home" if is_home else "default road"),
                "tv": _tv(game),
                "result": _result(game),
                "game_center": _game_center(game),
            }
        )
    games.sort(key=lambda item: (item["date"], item["start_utc"]))
    return games


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def build_ics(games: list[dict], stamp: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Jarvis//Buffalo Sabres 2026-27 Schedule//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Buffalo Sabres 2026-27",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for game in games:
        start = dt.datetime.fromisoformat(game["start_utc"].replace("Z", "+00:00"))
        end = start + dt.timedelta(hours=3)
        utc = dt.timezone.utc
        description = (
            f"Buffalo Sabres {game['game_type']}. "
            f"{game['day']}, {game['date']} at {game['start_et']}. "
            f"Opponent: {game['opponent']}. Site: {game['site']}. "
            f"Jersey: {game['jersey']}. "
            f"TV: {', '.join(game['tv']) or 'not announced'}."
        )
        if game["result"]:
            description += f" Result: {game['result']}."
        summary = "Sabres {0} {1} - {2}".format(
            game["site"][0], game["opponent_abbrev"], game["jersey"]
        )
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:sabres-20262027-{game['id']}@jarvis.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start.astimezone(utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end.astimezone(utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{_esc(summary)}",
                f"LOCATION:{_esc(game['venue'])}",
                f"DESCRIPTION:{_esc(description)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def build_markdown(games: list[dict], data: dict, stamp: str) -> str:
    regular = [g for g in games if g["game_type"] == "Regular Season"]
    pre = [g for g in games if g["game_type"] == "Preseason"]

    def jersey_block(jersey: str) -> list[str]:
        rows = [g for g in regular if g["jersey"] == jersey]
        out = [f"### {jersey} — {len(rows)} games", ""]
        for g in rows:
            verb = "vs" if g["site"] == "Home" else "at"
            out.append(
                f"- **{g['day']}, {g['date']}** — {g['site']} {verb} {g['opponent']} "
                f"at {g['venue']} ({g['start_et']})."
            )
        out.append("")
        return out

    def table(items: list[dict]) -> list[str]:
        out = [
            "| Date | Time (ET) | Opponent | Site | Venue | Jersey | TV | Result |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for g in items:
            out.append(
                "| {date} | {time} | {opp} | {site} | {venue} | {jersey} | {tv} | {result} |".format(
                    date=f"{g['day'][:3]} {g['date']}",
                    time=g["start_et"],
                    opp=g["opponent"],
                    site=g["site"],
                    venue=g["venue"],
                    jersey=g["jersey"],
                    tv=", ".join(g["tv"]) or "—",
                    result=g["result"] or "—",
                )
            )
        out.append("")
        return out

    opener, home_opener = regular[0], next(g for g in regular if g["site"] == "Home")
    finale = regular[-1]
    lines = [
        "# Buffalo Sabres — 2026-27 Schedule (local Jarvis knowledge)",
        "",
        f"Retrieved: {stamp}. All start times are Eastern (`America/New_York`).",
        f"Source data: {len(regular)} regular-season games, {len(pre)} preseason games.",
        "Local calendar feed: `notes/calendars/Buffalo-Sabres-2026-27.ics`.",
        "",
        "## Quick answers",
        "",
        f"- Season opener: **{opener['day']}, {opener['date']}** at {opener['opponent']}, "
        f"{opener['start_et']}, {opener['venue']}.",
        f"- Home opener: **{home_opener['day']}, {home_opener['date']}** vs {home_opener['opponent']}, "
        f"{home_opener['start_et']}, {home_opener['venue']}.",
        f"- Season finale: **{finale['day']}, {finale['date']}** vs {finale['opponent']}, "
        f"{finale['start_et']}, {finale['venue']}, jersey {finale['jersey']}.",
        "- Jerseys: Home Royal Blue at home and Road White on road by default; "
        "Black and Red Goathead for 10 home dates; Hometown Remix for 8 dates.",
        "- Jarvis can answer jersey questions: ask things like "
        "\"what jersey are the Sabres wearing on March 25?\".",
        "",
        "## Jersey schedule",
        "",
        "Default rule: **Home Royal Blue** at home, **Road White** away, except the dates below.",
        "",
    ]
    lines.extend(jersey_block(BLACK_RED))
    lines.extend(jersey_block(REMIX))
    lines.extend(
        [
            "## Full regular-season schedule",
            "",
            *table(regular),
            "## Preseason",
            "",
            *table(pre),
            "## Sources",
            "",
        ]
    )
    for source in data["sources"]:
        lines.append(f"- {source['name']}: {source['url']}")
    lines.append("")
    lines.append(
        "Notes: The official Sabres article states 10 black-and-red dates and 8 Hometown "
        "Remix dates. Its web text omits Oct. 10 and Nov. 4 from the embedded schedule image, "
        "so those rows are restored from ColorWay Sports and WBUF to match the announced totals."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    notes = Path(os.environ.get("JARVIS_NOTES_DIR", r"C:\Users\Justin\Documents\Default Project\notes"))
    calendars = notes / "calendars"
    calendars.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(API_URL, headers={"User-Agent": "JarvisScheduleBuilder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))

    stamp_dt = dt.datetime.now(dt.timezone.utc)
    stamp = stamp_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    games = build_games(raw)
    data = {
        "team": "Buffalo Sabres",
        "season": "2026-27",
        "timezone": "America/New_York",
        "retrieved_utc": stamp,
        "regular_season_games": sum(1 for g in games if g["game_type"] == "Regular Season"),
        "preseason_games": sum(1 for g in games if g["game_type"] == "Preseason"),
        "sources": SOURCES,
        "jersey_guide": {
            "default_home": HOME,
            "default_away": AWAY,
            BLACK_RED: [g["date"] for g in games if g["jersey"] == BLACK_RED],
            REMIX: [g["date"] for g in games if g["jersey"] == REMIX],
        },
        "games": games,
    }

    (notes / "Buffalo-Sabres-2026-27-Schedule.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (notes / "Buffalo-Sabres-2026-27-Schedule.md").write_text(
        build_markdown(games, data, stamp), encoding="utf-8"
    )
    (calendars / "Buffalo-Sabres-2026-27.ics").write_text(
        build_ics(games, stamp_dt.strftime("%Y%m%dT%H%M%SZ")), encoding="utf-8", newline=""
    )

    counts = {jersey: sum(1 for g in games if g["jersey"] == jersey) for jersey in (HOME, AWAY, BLACK_RED, REMIX)}
    print("wrote schedule files to", notes)
    print("games:", len(games), "jerseys:", counts)


if __name__ == "__main__":
    main()
