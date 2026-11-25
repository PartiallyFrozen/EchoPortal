"""Editable UI theme/plugins: created once, discovered safely, never overwritten."""
import config
import ui_custom


def test_ensure_creates_defaults_and_preserves_edits(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    ui_custom.ensure()

    theme = tmp_path / "ui" / "theme.css"
    blob = tmp_path / "ui" / "plugins" / "blob.js"
    assert theme.exists() and blob.exists()
    assert "--accent" in theme.read_text(encoding="utf-8")
    assert "Jarvis.plugin" in blob.read_text(encoding="utf-8")

    theme.write_text("/* mine */", encoding="utf-8")
    ui_custom.ensure()
    assert theme.read_text(encoding="utf-8") == "/* mine */"


def test_plugin_names_only_accepts_plain_js_files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    ui_custom.ensure()
    plugins = tmp_path / "ui" / "plugins"
    (plugins / "zz-custom.js").write_text("ok", encoding="utf-8")
    (plugins / "bad name.js").write_text("no", encoding="utf-8")
    (plugins / "../escape.js").write_text("no", encoding="utf-8")

    names = ui_custom.plugin_names()
    assert names == ["blob.js", "zz-custom.js"]


def test_read_plugin_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    ui_custom.ensure()
    assert ui_custom.read_plugin("blob.js").startswith("/* Jarvis plugin")
    assert ui_custom.read_plugin("../config.json") is None
    assert ui_custom.read_plugin("nope.js") is None
