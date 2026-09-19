"""The markets face reads its symbols from tickers.json, edited by the tray app while running."""
import json

import ticker


def test_defaults_when_there_is_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(ticker, "CONFIG", str(tmp_path / "missing.json"))
    assert ticker.load_config() == ticker.DEFAULT


def test_defaults_are_not_mutated_by_a_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(ticker, "CONFIG", str(tmp_path / "missing.json"))
    cfg = ticker.load_config()
    cfg["crypto"].append("DOGE")
    assert "DOGE" not in ticker.DEFAULT["crypto"]


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "tickers.json"
    monkeypatch.setattr(ticker, "CONFIG", str(path))
    ticker.save_config({"crypto": ["BTC"], "stocks": ["SHOP.TO"], "currency": "CAD", "interval": 30})
    cfg = ticker.load_config()
    assert cfg["crypto"] == ["BTC"]
    assert cfg["stocks"] == ["SHOP.TO"]
    assert cfg["currency"] == "CAD"
    assert cfg["interval"] == 30


def test_a_partial_config_keeps_the_other_defaults(tmp_path, monkeypatch):
    path = tmp_path / "tickers.json"
    path.write_text(json.dumps({"currency": "EUR"}), encoding="utf-8")
    monkeypatch.setattr(ticker, "CONFIG", str(path))
    cfg = ticker.load_config()
    assert cfg["currency"] == "EUR"
    assert cfg["crypto"] == ticker.DEFAULT["crypto"]


def test_malformed_json_does_not_take_the_agent_down(tmp_path, monkeypatch):
    path = tmp_path / "tickers.json"
    path.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(ticker, "CONFIG", str(path))
    assert ticker.load_config() == ticker.DEFAULT
