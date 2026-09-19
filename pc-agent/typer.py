"""Type text into a Windows window on behalf of the Spot's Voice face.

    focused_window()            -> (title, process_name) of the window that has focus
    type_text(text, enter, target)
        target = "focused" : paste into whatever has focus right now
        target = "claude"  : bring the Claude Code desktop window forward first

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


def _set_clipboard(text: str):
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            time.sleep(0.05)


def _key(vk, up=False):
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if up else 0, 0)


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
    _set_clipboard(text)
    time.sleep(0.05)
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
