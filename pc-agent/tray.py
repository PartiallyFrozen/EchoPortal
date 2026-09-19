"""EchoPortal Agent tray app: runs the Echo Spot agent in the background with a system-tray icon.

    pythonw tray.py            (no console window)

Right-click menu: status, Start/Stop, Send test banner, Open log, Start with Windows, Quit.
"""
import asyncio
import json
import os
import sys
import threading
import webbrowser

from PIL import Image, ImageDraw
import pystray

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE
LOG = os.path.join(DATA_DIR, "agent.log")
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_FILE = os.path.join(STARTUP_DIR, "EchoPortal Agent.vbs")

# pythonw has no stdout/stderr; send the agent's prints to a log file instead
if sys.stdout is None or sys.stderr is None or not sys.stdout.isatty():
    _log = open(LOG, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log

import agent  # noqa: E402  (imports after logging is redirected so its startup prints land in the log)


class Runner:
    """Owns the asyncio loop thread that runs agent.main()."""

    def __init__(self):
        self.loop = None
        self.task = None
        self.thread = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="rook-agent", daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(agent.main())
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            print("agent crashed:", repr(e))
        finally:
            self.loop.close()
            print("agent stopped")

    def stop(self):
        if not self.running:
            return
        self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(timeout=5)


runner = Runner()


def make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=color, width=7)
    d.ellipse((22, 22, 42, 42), fill=color)
    return img


ICON_ON = make_icon("#4FC3F7")
ICON_OFF = make_icon("#78909C")


def status_text(_item=None) -> str:
    if runner.running:
        n = len(agent.CLIENTS)
        return f"EchoPortal Agent: running  ({n} client{'s' if n != 1 else ''})"
    return "EchoPortal Agent: stopped"


def toggle(icon, _item):
    if runner.running:
        runner.stop()
    else:
        runner.start()
    refresh(icon)


def refresh(icon):
    icon.icon = ICON_ON if runner.running else ICON_OFF
    icon.title = status_text()
    icon.update_menu()


def test_banner(_icon, _item):
    """Publish a banner on the agent's own hub. In-process: a frozen build has no notify.py to run."""
    import threading

    def go():
        import asyncio

        import websockets

        async def send():
            async with websockets.connect("ws://127.0.0.1:%d" % agent.PORT) as ws:
                await ws.send(json.dumps({"ch": "notify", "data": {
                    "title": "EchoPortal", "text": "test banner from the tray", "face": "pcmon",
                    "ttl": 8000, "chime": True}}))

        try:
            asyncio.run(send())
        except Exception as e:  # noqa: BLE001
            print("tray: could not send the test banner:", e)

    threading.Thread(target=go, daemon=True).start()


def open_log(_icon, _item):
    if not os.path.exists(LOG):
        open(LOG, "a").close()
    webbrowser.open(LOG)


def autostart_enabled(_item=None) -> bool:
    return os.path.exists(STARTUP_FILE)


def toggle_autostart(icon, _item):
    if autostart_enabled():
        os.remove(STARTUP_FILE)
    else:
        target = f'""{sys.executable}""' if FROZEN else f'""{sys.executable.replace("python.exe", "pythonw.exe")}"" ""{os.path.join(HERE, "tray.py")}""'
        with open(STARTUP_FILE, "w", encoding="utf-8") as f:
            f.write(f'CreateObject("WScript.Shell").Run "{target}", 0, False\n')
    icon.update_menu()


def edit_tickers(_icon, _item):
    """Small Tk dialog to edit tickers.json; the agent reloads it within a second."""
    import threading

    def ui():
        import tkinter as tk
        from tkinter import ttk
        import ticker
        cfg = ticker.load_config()
        root = tk.Tk()
        root.title("EchoPortal — Tickers")
        root.attributes("-topmost", True)
        frm = ttk.Frame(root, padding=12)
        frm.grid()
        ttk.Label(frm, text="Crypto symbols (comma separated)").grid(row=0, column=0, sticky="w")
        e1 = ttk.Entry(frm, width=48); e1.insert(0, ", ".join(cfg.get("crypto", []))); e1.grid(row=1, column=0, pady=(0, 8))
        ttk.Label(frm, text="Stock symbols (Yahoo tickers, e.g. NVDA, AAPL, SHOP.TO)").grid(row=2, column=0, sticky="w")
        e2 = ttk.Entry(frm, width=48); e2.insert(0, ", ".join(cfg.get("stocks", []))); e2.grid(row=3, column=0, pady=(0, 8))
        ttk.Label(frm, text="Crypto currency (USD, CAD, EUR ...)").grid(row=4, column=0, sticky="w")
        e3 = ttk.Entry(frm, width=12); e3.insert(0, cfg.get("currency", "USD")); e3.grid(row=5, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frm, text="Stock refresh (seconds; crypto streams live)").grid(row=6, column=0, sticky="w")
        e4 = ttk.Entry(frm, width=12); e4.insert(0, str(cfg.get("stock_interval", 10))); e4.grid(row=7, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frm, text="Auto-cycle pages every (seconds, 0 = off)").grid(row=8, column=0, sticky="w")
        e5 = ttk.Entry(frm, width=12); e5.insert(0, str(cfg.get("cycle_seconds", 8))); e5.grid(row=9, column=0, sticky="w", pady=(0, 12))

        def save():
            def num(entry, default, lo):
                try:
                    return max(lo, int(entry.get().strip() or default))
                except ValueError:
                    return default
            ticker.save_config({
                "crypto": [x.strip().upper() for x in e1.get().split(",") if x.strip()],
                "stocks": [x.strip().upper() for x in e2.get().split(",") if x.strip()],
                "currency": (e3.get().strip() or "USD").upper(),
                "stock_interval": num(e4, 10, 3),
                "cycle_seconds": num(e5, 8, 0),
            })
            root.destroy()

        btns = ttk.Frame(frm); btns.grid(row=10, column=0, sticky="e")
        ttk.Button(btns, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text="Save", command=save).grid(row=0, column=1)
        root.mainloop()

    threading.Thread(target=ui, daemon=True).start()


def _tell_agent(data: dict):
    """One-shot message to the running agent (same path notify.py uses)."""
    import websockets

    async def go():
        async with websockets.connect("ws://127.0.0.1:8765") as ws:
            await ws.send(json.dumps({"ch": "bg", "data": data}))

    try:
        asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        print("tray: could not reach the agent:", e)


def pick_background(_icon, _item):
    """Choose a picture for the Spot's global background: centre-cropped to 480x480 JPEG, pushed via the agent."""
    import threading

    def ui():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title="EchoPortal — background picture",
                                          filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All files", "*.*")])
        root.destroy()
        if not path:
            return
        im = Image.open(path).convert("RGB")
        w, h = im.size; side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side)).resize((480, 480), Image.LANCZOS)
        im.save(agent.BG_FILE, "JPEG", quality=88)
        print("tray: background picture set from", path)
        _tell_agent({"op": "reload"})

    threading.Thread(target=ui, daemon=True).start()


def clear_background(_icon, _item):
    _tell_agent({"op": "clear"})


def quit_app(icon, _item):
    runner.stop()
    icon.stop()


def main():
    runner.start()
    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda _i: "Stop agent" if runner.running else "Start agent", toggle),
        pystray.MenuItem("Send test banner to Spot", test_banner),
        pystray.MenuItem("Open log", open_log),
        pystray.MenuItem("Tickers…", edit_tickers),
        pystray.MenuItem("Background picture…", pick_background),
        pystray.MenuItem("Clear background picture", clear_background),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=autostart_enabled),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("rook-agent", ICON_ON, status_text(), menu)

    def ticker():
        # keep the tooltip / client count fresh
        import time
        while icon.visible or True:
            time.sleep(5)
            try:
                refresh(icon)
            except Exception:  # noqa: BLE001
                return

    threading.Thread(target=ticker, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
