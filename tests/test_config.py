"""Per-machine agent settings: location, ComfyUI URLs, ports."""
import json

import config


def test_defaults_have_no_location(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "EXAMPLE", str(tmp_path / "missing-example.json"))
    monkeypatch.setattr(config, "VALUES", config._read())
    assert config.location() == (None, None, "auto")
    assert config.has_location() is False


def test_a_config_file_is_merged_over_the_defaults(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"latitude": 51.5, "longitude": -0.12, "timezone": "Europe/London"}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(path))
    monkeypatch.setattr(config, "VALUES", config._read())
    assert config.location() == (51.5, -0.12, "Europe/London")
    assert config.has_location() is True
    assert config.get("hub_port") == 8765, "unset keys keep their default"


def test_the_first_run_copies_the_example(monkeypatch, tmp_path):
    example = tmp_path / "config.example.json"
    example.write_text(json.dumps({"latitude": 1.5, "longitude": 2.5, "timezone": "UTC"}), encoding="utf-8")
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG", str(target))
    monkeypatch.setattr(config, "EXAMPLE", str(example))
    values = config._read()
    assert target.exists(), "the example is copied so there is a file to edit"
    assert values["latitude"] == 1.5


def test_malformed_json_falls_back_to_defaults(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(path))
    monkeypatch.setattr(config, "VALUES", config._read())
    assert config.has_location() is False
    assert config.get("media_port") == 8766


def test_null_values_fall_back_rather_than_returning_none(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"timezone": None}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(path))
    monkeypatch.setattr(config, "VALUES", config._read())
    assert config.get("timezone", "auto") == "auto"
