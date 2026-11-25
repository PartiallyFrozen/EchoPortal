"""Typing: a failed clipboard write or a focus change must not be reported as success."""
import pytest

import typer


@pytest.fixture
def fake_win(monkeypatch):
    state = {"handles": [111, 111], "clipboard": "old", "set_ok": True, "keys": []}
    monkeypatch.setattr(typer.win32gui, "GetForegroundWindow", lambda: state["handles"].pop(0))
    monkeypatch.setattr(typer.win32gui, "GetWindowText", lambda h: "window-%d" % h)
    monkeypatch.setattr(typer, "_proc_name", lambda h: "proc-%d" % h)
    monkeypatch.setattr(typer, "_get_clipboard", lambda: state["clipboard"])
    monkeypatch.setattr(typer, "_set_clipboard", lambda text: state["set_ok"])
    monkeypatch.setattr(typer, "_key", lambda vk, up=False: state["keys"].append((vk, up)))
    monkeypatch.setattr(typer.time, "sleep", lambda _s: None)
    return state


def test_type_text_reports_a_clipboard_failure(fake_win):
    fake_win["set_ok"] = False
    result = typer.type_text("hello", enter=False)
    assert result["ok"] is False
    assert "clipboard" in result["error"]
    assert not [k for k in fake_win["keys"] if k[0] == typer.VK_V]   # nothing was pasted


def test_type_text_aborts_if_focus_changes(fake_win):
    fake_win["handles"] = [111, 222]       # captured, then stolen before the paste
    result = typer.type_text("hello", enter=False)
    assert result["ok"] is False
    assert "focus changed" in result["error"]
    assert not [k for k in fake_win["keys"] if k[0] == typer.VK_V]   # nothing was pasted


def test_type_text_pastes_when_focus_holds(fake_win):
    result = typer.type_text("hello", enter=False)
    assert result["ok"] is True
    assert result["window"] == "window-111"
    assert fake_win["keys"]                # Ctrl+V was sent
