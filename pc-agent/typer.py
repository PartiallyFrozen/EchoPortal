"""Type text into a Windows window on behalf of the Spot's Voice face.

    focused_window()            -> (title, process_name) of the window that has focus
    type_text(text, enter, target)
        target = "focused" : paste into whatever has focus right now
        target = "claude"  : bring the Claude Code desktop window forward first
    list_windows(limit)         -> visible titled windows as {title, process, hwnd} dicts
    focus_window(title)         -> focus a visible window by exact, then substring, title match
    close_window(title)         -> post WM_CLOSE to the best matching visible window
    media_control(action)       -> tap a media key (play_pause/next/previous/volume/mute)

Pastes via the clipboard (instant, Unicode-safe) and restores the previous clipboard contents.
"""
import time

import psutil
import win32api
import win32clipboard
import win32con
import win32gui
import win32process

VK_CONTROL, VK_MENU, VK_RETURN, VK_V = 0x11, 0x12, 0x0D, 0x56
VK_MEDIA_NEXT_TRACK, VK_MEDIA_PREV_TRACK = 0xB0, 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF

_MEDIA_KEYS = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "volume_up": VK_VOLUME_UP,
    "volume_down": VK_VOLUME_DOWN,
    "mute": VK_VOLUME_MUTE,
}


def _proc_name(hwnd) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return "?"


def focused_window() -> tuple[str, str]:
    h = win32gui.GetForegroundWindow()
    return win32gui.GetWindowText(h), _proc_name(h)


def find_claude_window():
    """The Claude Code desktop app: process claude.exe with a visible top-level window."""
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) and _proc_name(h).lower() == "claude.exe":
            found.append(h)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _find_window(title: str):
    """Visible titled window matching *title*: exact case-insensitive match wins, then substring.

    EnumWindows walks top-level windows in Z order, so ties resolve to the frontmost one.
    """
    needle = (title or "").strip().casefold()
    if not needle:
        return None
    exact, partial = None, None

    def cb(h, _):
        nonlocal exact, partial
        if not win32gui.IsWindowVisible(h):
            return
        text = win32gui.GetWindowText(h)
        if not text:
            return
        lowered = text.casefold()
        if lowered == needle:
            if exact is None:
                exact = h
        elif needle in lowered and partial is None:
            partial = h

    win32gui.EnumWindows(cb, None)
    return exact if exact is not None else partial


def list_windows(limit: int = 20) -> list[dict]:
    """Visible top-level windows with non-empty titles, in enumeration order."""
    found = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        name = win32gui.GetWindowText(h)
        if name:
            found.append({"title": name, "process": _proc_name(h), "hwnd": int(h)})

    win32gui.EnumWindows(cb, None)
    return found[:max(0, limit)]


def _focus(hwnd) -> bool:
    """SetForegroundWindow from a background process needs the ALT-tap trick."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    for _ in range(3):
        win32api.keybd_event(VK_MENU, 0, 0, 0)
        win32api.keybd_event(VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.08)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return win32gui.GetForegroundWindow() == hwnd


def focus_window(title: str) -> dict:
    """Bring the best matching visible window forward. Returns the standard status dict."""
    hwnd = _find_window(title)
    if hwnd is None:
        return {"ok": False, "window": title, "process": "", "error": "window not found: %s" % title}
    name, proc = win32gui.GetWindowText(hwnd), _proc_name(hwnd)
    try:
        ok = _focus(hwnd)
        error = None if ok else "could not focus window"
    except Exception as exc:  # noqa: BLE001
        ok, error = False, str(exc)
    return {"ok": bool(ok), "window": name, "process": proc, "error": error}


def close_window(title: str) -> dict:
    """Ask the best matching visible window to close with WM_CLOSE."""
    hwnd = _find_window(title)
    if hwnd is None:
        return {"ok": False, "window": title, "process": "", "error": "window not found: %s" % title}
    name, proc = win32gui.GetWindowText(hwnd), _proc_name(hwnd)
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "window": name, "process": proc, "error": str(exc)}
    return {"ok": True, "window": name, "process": proc, "error": None}


def _get_clipboard():
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        return None


def _set_clipboard(text: str) -> bool:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    return False


def _key(vk, up=False):
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if up else 0, 0)


def media_control(action: str) -> dict:
    """Tap one media key (press + release). Returns {"ok", "action", "error"}."""
    normalized = (action or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized not in _MEDIA_KEYS:
        return {"ok": False, "action": normalized, "error": "unknown media action: %s" % action}
    vk = _MEDIA_KEYS[normalized]
    try:
        win32api.keybd_event(vk, 0, 0, 0)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": normalized, "error": str(exc)}
    return {"ok": True, "action": normalized, "error": None}


def type_text(text: str, enter: bool = True, target: str = "focused") -> dict:
    """Returns {"ok": bool, "window": title, "process": name, "error": str|None}."""
    if target == "claude":
        h = find_claude_window()
        if h is None:
            return {"ok": False, "window": "", "process": "", "error": "Claude Code window not found"}
        if not _focus(h):
            return {"ok": False, "window": win32gui.GetWindowText(h), "process": "claude.exe", "error": "could not focus Claude window"}
        time.sleep(0.15)
    hwnd = win32gui.GetForegroundWindow()
    title, proc = win32gui.GetWindowText(hwnd), _proc_name(hwnd)

    # release any modifier the user might be holding, then paste
    for vk in (VK_CONTROL, VK_MENU, win32con.VK_SHIFT):
        _key(vk, up=True)
    previous = _get_clipboard()
    if not _set_clipboard(text):
        return {"ok": False, "window": title, "process": proc, "error": "could not write to the clipboard"}
    time.sleep(0.05)
    # The user (or another app) can steal focus between the announcement and the paste. Do not
    # paste into the wrong window and call it a success.
    now = win32gui.GetForegroundWindow()
    if now != hwnd:
        if previous is not None:
            _set_clipboard(previous)
        return {"ok": False, "window": win32gui.GetWindowText(now), "process": _proc_name(now),
                "error": "focus changed before pasting"}
    _key(VK_CONTROL); _key(VK_V); _key(VK_V, up=True); _key(VK_CONTROL, up=True)
    time.sleep(0.12)
    if enter:
        _key(VK_RETURN); _key(VK_RETURN, up=True)
    time.sleep(0.15)
    if previous is not None:
        _set_clipboard(previous)
    return {"ok": True, "window": title, "process": proc, "error": None}


if __name__ == "__main__":
    print("focused:", focused_window())
    print("claude window:", find_claude_window())
