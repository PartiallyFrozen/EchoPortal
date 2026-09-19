"""Agent settings that differ per machine: where you are, and where ComfyUI lives.

Read from `config.json` next to the agent. On first run it is created from
`packaging/config.example.json`, so a fresh install has a file to edit rather than a constant to
hunt for in the source. Values are read once at import; restart the agent from the tray after
editing.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Frozen by PyInstaller: the code is in _internal/, the user's files are next to the exe, which is
# where the installer put config.json and where someone looking for it will look.
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE


def data_path(*parts) -> str:
    """A file the user or the agent writes: config, tickers, log, media, rendered art."""
    return os.path.join(DATA_DIR, *parts)


def bundled_path(*parts) -> str:
    """A file shipped with the code, read-only."""
    return os.path.join(HERE, *parts)


CONFIG = data_path("config.json")
EXAMPLE = data_path("packaging", "config.example.json") if FROZEN else os.path.join(HERE, "..", "packaging", "config.example.json")

DEFAULT = {
    # No sensible default exists for a location, so the weather faces stay quiet until one is set.
    "latitude": None,
    "longitude": None,
    "timezone": "auto",
    "comfy_urls": ["http://127.0.0.1:8188", "http://127.0.0.1:8000"],
    "hub_port": 8765,
    "media_port": 8766,
    "claude_hook_port": 8767,
}


def _read() -> dict:
    if not os.path.exists(CONFIG) and os.path.exists(EXAMPLE):
        try:
            shutil.copyfile(EXAMPLE, CONFIG)
            print("config: created", CONFIG, "- set your location in it")
        except OSError as e:
            print("config: could not create config.json:", e)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return {**DEFAULT, **json.load(f)}
    except FileNotFoundError:
        return dict(DEFAULT)
    except (OSError, json.JSONDecodeError) as e:
        print("config: %s is not readable (%s); using defaults" % (os.path.basename(CONFIG), e))
        return dict(DEFAULT)


VALUES = _read()


def get(name: str, fallback=None):
    v = VALUES.get(name, fallback)
    return fallback if v is None else v


def location() -> tuple:
    """(latitude, longitude, timezone) or (None, None, tz) when it has not been set."""
    return VALUES.get("latitude"), VALUES.get("longitude"), get("timezone", "auto")


def has_location() -> bool:
    lat, lon, _tz = location()
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float))
