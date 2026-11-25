"""The local Sabres schedule is queryable without relying on note snippets."""
import datetime as dt
import json

import sabres_schedule

DATA = {
    "games": [
        {
            "date": "2026-10-01", "day": "Thursday", "start_utc": "2026-10-01T23:00:00Z",
            "start_et": "7:00 PM EDT", "opponent": "Columbus Blue Jackets", "opponent_abbrev": "CBJ",
            "site": "Away", "venue": "Nationwide Arena", "jersey": "Road White",
            "tv": ["ESPN+"], "result": "", "game_type": "Regular Season",
        },
        {
            "date": "2026-11-25", "day": "Wednesday", "start_utc": "2026-11-26T00:00:00Z",
            "start_et": "7:00 PM EST", "opponent": "New York Rangers", "opponent_abbrev": "NYR",
            "site": "Home", "venue": "KeyBank Center", "jersey": "Hometown Remix",
            "tv": [], "result": "", "game_type": "Regular Season",
        },
        {
            "date": "2027-03-25", "day": "Thursday", "start_utc": "2027-03-25T23:00:00Z",
            "start_et": "7:00 PM EDT", "opponent": "Toronto Maple Leafs", "opponent_abbrev": "TOR",
            "site": "Home", "venue": "KeyBank Center", "jersey": "Black and Red Goathead",
            "tv": [], "result": "", "game_type": "Regular Season",
        },
        {
            "date": "2026-09-21", "day": "Monday", "start_utc": "2026-09-21T23:00:00Z",
            "start_et": "7:00 PM EDT", "opponent": "Pittsburgh Penguins", "opponent_abbrev": "PIT",
            "site": "Away", "venue": "PPG Paints Arena", "jersey": "Road White",
            "tv": [], "result": "", "game_type": "Preseason",
        },
    ]
}


def _path(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(DATA), encoding="utf-8")
    return path


def test_date_and_jersey_lookup(tmp_path):
    path = _path(tmp_path)
    result = sabres_schedule.lookup("what jersey are the Sabres wearing on March 25?", path=path)
    assert result["ok"] is True and result["count"] == 1
    game = result["games"][0]
    assert game["date"] == "2027-03-25"
    assert game["jersey"] == "Black and Red Goathead"
    assert "Goathead" in result["summary"]


def test_summary_includes_tv_when_available(tmp_path):
    result = sabres_schedule.lookup("season opener", path=_path(tmp_path))
    assert result["ok"] is True
    assert "TV: ESPN+" in result["summary"]
    assert "Nationwide Arena" in result["summary"]


def test_jersey_question_returns_only_alternates(tmp_path):
    path = _path(tmp_path)
    result = sabres_schedule.lookup("what jerseys do the Sabres wear?", path=path)
    assert [g["jersey"] for g in result["games"]] == ["Hometown Remix", "Black and Red Goathead"]


def test_opponent_lookup(tmp_path):
    path = _path(tmp_path)
    result = sabres_schedule.lookup("when do the Sabres play Toronto?", path=path)
    assert result["count"] == 1
    assert result["games"][0]["opponent"] == "Toronto Maple Leafs"


def test_next_game_skips_past_games(tmp_path):
    path = _path(tmp_path)
    result = sabres_schedule.lookup("next Sabres game", path=path, now=dt.date(2026, 11, 24))
    assert result["count"] == 1
    assert result["games"][0]["date"] == "2026-11-25"


def test_specials_and_full_schedule(tmp_path):
    path = _path(tmp_path)
    opener = sabres_schedule.lookup("when is the season opener?", path=path)
    assert opener["games"][0]["date"] == "2026-10-01"

    full = sabres_schedule.lookup("show the full schedule", path=path)
    assert full["count"] == 4
    assert any(g["game_type"] == "Preseason" for g in full["games"])


def test_missing_file_is_reported(tmp_path):
    result = sabres_schedule.lookup("Sabres", path=tmp_path / "missing.json")
    assert result["ok"] is False and "unavailable" in result["error"]
