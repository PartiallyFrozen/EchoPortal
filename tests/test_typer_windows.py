"""Window enumeration/focus/close and media keys: every Win32 call is faked here."""
import pytest

import typer


@pytest.fixture
def fake_windows(monkeypatch):
    # hwnd -> (visible, title), mirroring what Win32 reports for top-level windows.
    windows = {
        11: (True, "Notepad"),
        22: (True, "Visual Studio Code - main.py"),
        33: (False, "Hidden utility"),
        44: (True, ""),
        55: (True, "notepad - untitled"),
    }
    order = [11, 22, 33, 44, 55]
    calls = {"focused": [], "posted": []}

    monkeypatch.setattr(typer.win32gui, "EnumWindows", lambda cb, _: [cb(h, None) for h in order])
    monkeypatch.setattr(typer.win32gui, "IsWindowVisible", lambda h: windows[h][0])
    monkeypatch.setattr(typer.win32gui, "GetWindowText", lambda h: windows[h][1])
    monkeypatch.setattr(typer, "_proc_name", lambda h: "proc-%d" % h)
    monkeypatch.setattr(typer, "_focus", lambda h: calls["focused"].append(h) or True)
    monkeypatch.setattr(typer.win32gui, "PostMessage", lambda h, msg, wp, lp: calls["posted"].append((h, msg, wp, lp)))
    return calls


def test_list_windows_skips_hidden_and_untitled(fake_windows):
    result = typer.list_windows()
    assert [w["hwnd"] for w in result] == [11, 22, 55]
    assert result[0] == {"title": "Notepad", "process": "proc-11", "hwnd": 11}
    assert all(isinstance(w["hwnd"], int) for w in result)


def test_list_windows_honours_limit(fake_windows):
    assert [w["hwnd"] for w in typer.list_windows(limit=2)] == [11, 22]
    assert typer.list_windows(limit=0) == []


def test_focus_window_prefers_exact_case_insensitive_match(fake_windows):
    result = typer.focus_window("  NOTEPAD  ")
    assert result == {"ok": True, "window": "Notepad", "process": "proc-11", "error": None}
    assert fake_windows["focused"] == [11]  # not the "notepad - untitled" substring


def test_focus_window_falls_back_to_substring(fake_windows):
    result = typer.focus_window("untitled")
    assert result == {"ok": True, "window": "notepad - untitled", "process": "proc-55", "error": None}
    assert fake_windows["focused"] == [55]


def test_focus_window_reports_missing_and_failed_focus(fake_windows, monkeypatch):
    missing = typer.focus_window("chrome")
    assert missing == {"ok": False, "window": "chrome", "process": "", "error": "window not found: chrome"}
    assert fake_windows["focused"] == []

    monkeypatch.setattr(typer, "_focus", lambda h: False)
    failed = typer.focus_window("Visual Studio")
    assert failed["ok"] is False
    assert failed["window"] == "Visual Studio Code - main.py"
    assert "focus" in failed["error"]


def test_close_window_posts_wm_close(fake_windows):
    result = typer.close_window("visual studio code")
    assert result == {
        "ok": True,
        "window": "Visual Studio Code - main.py",
        "process": "proc-22",
        "error": None,
    }
    assert fake_windows["posted"] == [(22, typer.win32con.WM_CLOSE, 0, 0)]


def test_close_window_does_nothing_when_not_found(fake_windows):
    result = typer.close_window("not here")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert fake_windows["posted"] == []


@pytest.fixture
def fake_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(typer.win32api, "keybd_event", lambda vk, scan, flags, extra: calls.append((vk, flags)))
    return calls


def test_media_control_sends_press_then_release(fake_keys):
    result = typer.media_control("play_pause")
    assert result == {"ok": True, "action": "play_pause", "error": None}
    assert fake_keys == [(0xB3, 0), (0xB3, typer.win32con.KEYEVENTF_KEYUP)]


@pytest.mark.parametrize(
    ("action", "vk"),
    [
        ("next", 0xB0),
        ("previous", 0xB1),
        ("volume_up", 0xAF),
        ("volume_down", 0xAE),
        ("mute", 0xAD),
    ],
)
def test_media_control_supported_actions(fake_keys, action, vk):
    result = typer.media_control(action.upper())
    assert result == {"ok": True, "action": action, "error": None}
    assert fake_keys == [(vk, 0), (vk, typer.win32con.KEYEVENTF_KEYUP)]


def test_media_control_rejects_unknown_action(fake_keys):
    result = typer.media_control("launch_rockets")
    assert result["ok"] is False
    assert result["action"] == "launch_rockets"
    assert "unknown" in result["error"].lower()
    assert fake_keys == []


def test_media_control_reports_keybd_event_failure(monkeypatch):
    def boom(*_args):
        raise RuntimeError("no keyboard")

    monkeypatch.setattr(typer.win32api, "keybd_event", boom)
    result = typer.media_control("mute")
    assert result["ok"] is False
    assert result["action"] == "mute"
    assert "no keyboard" in result["error"]
