"""Switch Jarvis's calendar channel from remote ICS URLs to local copies.

Downloads every configured ``calendar_urls`` feed into the knowledge folder,
adds the local Sabres schedule feed, backs up config.json, then points the
agent at the local files. No secrets are printed by this script.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "EchoPortal" / "config.json"
NOTES = Path(os.environ.get("JARVIS_NOTES_DIR", r"C:\Users\Justin\Documents\Default Project\notes"))
CALENDARS = NOTES / "calendars"
SABRES = CALENDARS / "Buffalo-Sabres-2026-27.ics"
SABRES_JSON = NOTES / "Buffalo-Sabres-2026-27-Schedule.json"


def _plain(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return (cleaned[:60] or fallback).lower()


def _calendar_name(text: str, fallback: str) -> str:
    try:
        from icalendar import Calendar

        cal = Calendar.from_ical(text)
        return str(cal.get("X-WR-CALNAME") or fallback)
    except Exception:  # noqa: BLE001 - a malformed feed still gets a safe filename
        return fallback


def _jersey_map() -> dict[dt.datetime, str]:
    if not SABRES_JSON.exists():
        return {}
    data = json.loads(SABRES_JSON.read_text(encoding="utf-8"))
    result: dict[dt.datetime, str] = {}
    for game in data.get("games") or []:
        raw = str(game.get("start_utc") or "")
        if raw:
            result[dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))] = str(game.get("jersey") or "")
    return result


def _enrich_nhl_feed(path: Path, jerseys: dict[dt.datetime, str]) -> int:
    """Append Sabres jersey names to the existing NHL calendar instead of duplicating games."""
    from icalendar import Calendar

    cal = Calendar.from_ical(path.read_text(encoding="utf-8-sig"))
    changed = 0
    for event in cal.walk("VEVENT"):
        summary = str(event.get("summary") or "")
        start = event.get("dtstart")
        if "Buffalo Sabres" not in summary or start is None:
            continue
        moment = start.dt
        if hasattr(moment, "astimezone"):
            moment = moment.astimezone(dt.timezone.utc)
        jersey = jerseys.get(moment)
        if not jersey or jersey in summary:
            continue
        event["summary"] = f"{summary} - {jersey}"
        changed += 1
    if changed:
        path.write_bytes(cal.to_ical())
    return changed


def main() -> None:
    CALENDARS.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    urls = config.get("calendar_urls") or []
    if isinstance(urls, str):
        urls = [urls]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CONFIG_PATH.with_name(f"config.json.bak-localcal-{stamp}")
    backup.write_text(CONFIG_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8")

    local_paths: list[str] = []
    nhl_path: Path | None = None
    for index, url in enumerate(urls, start=1):
        url = str(url).strip()
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://", "webcal://")):
            existing = Path(url)
            if existing.resolve() == SABRES.resolve():
                continue  # Archive only; the enriched NHL feed avoids duplicate games.
            local_paths.append(url)
            if existing.exists():
                label = _calendar_name(existing.read_text(encoding="utf-8-sig"), existing.stem)
                if _plain(label) == "nhl":
                    nhl_path = existing
            continue
        fetch_url = "https://" + url[8:] if url.lower().startswith("webcal://") else url
        request = urllib.request.Request(fetch_url, headers={"User-Agent": "JarvisCalendarLocalizer/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
        label = _calendar_name(text, f"calendar-{index}")
        filename = f"{index:02d}-{_safe_name(label, f'calendar-{index}')}.ics"
        path = CALENDARS / filename
        path.write_text(text, encoding="utf-8", newline="")
        local_paths.append(str(path))
        if _plain(label) == "nhl":
            nhl_path = path
        print(f"localized calendar {index}: {label} -> {filename}")

    if nhl_path is not None:
        enriched = _enrich_nhl_feed(nhl_path, _jersey_map())
        print(f"added jersey names to {enriched} Sabres event(s) in {nhl_path.name}")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    config["calendar_urls"] = [p for p in local_paths if not (p in seen or seen.add(p))]
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    (CALENDARS / "README.txt").write_text(
        "Jarvis local calendar files\n"
        "===========================\n\n"
        "These are local, read-only ICS snapshots. Jarvis reads them with the same\n"
        "calendar tool it used for the Google URLs; no network request is made.\n\n"
        "To refresh a Google calendar, download its secret iCal URL again and replace\n"
        "the matching .ics file. The previous config (with the original URLs) is stored\n"
        f"next to config.json as {backup.name}.\n\n"
        "Buffalo-Sabres-2026-27.ics is the full 2026-27 Sabres schedule archive. The\n"
        "active NHL calendar above was enriched with Sabres jersey names so games are\n"
        "not duplicated in Jarvis's agenda.\n",
        encoding="utf-8",
    )
    print(f"wrote {len(config['calendar_urls'])} local calendar paths to config.json")
    print("backup:", backup.name)


if __name__ == "__main__":
    main()
