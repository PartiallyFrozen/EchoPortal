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


def ensure_token() -> str:
    """Make sure the LAN ports are never started without a shared token.

    Source builds historically used an empty token as "trust the LAN". That is no longer safe
    because the hub can type into the focused Windows window. A fresh install therefore gets a
    random token written back to config.json. If config.json is read-only, fail closed rather
    than silently opening an unauthenticated hub.
    """
    token = str(VALUES.get("api_token", "") or "").strip()
    if token:
        return token
    import secrets

    token = secrets.token_urlsafe(24)
    VALUES["api_token"] = token
    tmp = CONFIG + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(VALUES, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, CONFIG)
    except Exception as e:  # noqa: BLE001 - a config problem must not become an open hub
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(
            "api_token is empty and config.json is not writable; refusing to start. "
            "Set api_token in config.json (the same value goes on the Spot)."
        ) from e
    print("api_token was empty; generated a new token in config.json")
    return token


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
    # "auto" uses Gemini when a key is present, otherwise local Whisper.
    "voice_backend": "auto",
    "gemini_api_key": "",
    # An alias, not a version: Google retires dated models (gemini-2.5-flash now 404s for new
    # keys), and the -latest alias follows the replacement automatically.
    "gemini_model": "gemini-flash-lite-latest",
    # Calendar: read-only secret ICS URLs (Google/Outlook/Fastmail), refreshed every 15 min.
    "calendar_urls": [],
    "calendar_days": 7,
    "calendar_refresh_min": 15,
    # Proactive briefings: disabled until explicitly enabled. Times are local wall-clock HH:MM.
    "briefing_enabled": False,
    "briefing_morning_time": "07:00",
    "briefing_evening_time": "21:00",
    "briefing_face": "assistant",
    "briefing_speak": True,
    "briefing_chime": True,
    "briefing_ttl": 12000,
    # Alerts: see alerts.py for the rule shape. Empty means no banners.
    "alerts": [],
    # Wake-up light: how long the screen takes to reach full brightness after a wake alarm.
    "wake_ramp_minutes": 20,
    # Optional shared token for the WebSocket hub, HTTP API and /media/. Empty trusts the LAN.
    # Set the same value on the Spot (long-press -> "PC hub token...").
    "api_token": "",
    # File inside media/ that the Video Clock face loops. Informational: the Spot stores its own
    # copy of this name (long-press -> "Video clip...") so it can be changed without a rebuild.
    "media_clip": "fireplace.mp4",
    # Optional server-side workers. Remove a face from this list to stop its polling entirely.
    # "markets" and "claude" are deliberately absent: their faces are not registered by default.
    "faces": ["clock", "videoclock", "pcmon", "voice", "assistant", "comfy", "timer", "calendar", "sky"],
    # Jarvis assistant. hybrid tries Gemini first and falls back to a local Ollama-compatible model.
    "assistant_backend": "hybrid",
    "assistant_model_choice": "hybrid",
    "assistant_gemini_model": "gemini-flash-lite-latest",
    "assistant_local_url": "http://127.0.0.1:11434",
    "assistant_local_model": "qwen3:14b",
    "assistant_keep_alive": "10m",  # keep the local model loaded between turns
    "assistant_speak": True,
    # Speak each completed sentence while the model is still writing the rest.
    "assistant_stream_tts": True,
    "assistant_pause_marker": "[pause]",  # optional spoken pause; stripped from visible text
    # Spoken voice. "auto" uses the bundled Piper neural voice (British male) when present and
    # falls back to Windows SAPI. "jarvis" adds measured phrasing; "natural" keeps the base prompt.
    "tts_backend": "auto",   # "auto", "piper" or "sapi"
    "tts_piper_model": "en_GB-alan-medium.onnx",
    "tts_piper_length_scale": 0.95,  # >1 slower, <1 faster
    "tts_style": "jarvis",
    "tts_voice": "",       # exact SAPI voice name; blank picks the best voice for the style
    "tts_rate": None,      # optional override, -10..10 (negative is slower)
    "tts_pitch": None,     # optional override, -8..8 semitones (negative is deeper)
    "tts_volume": None,    # optional override, 0..100
    # Confirmation UX. "always" stores a tool+argument signature here so it stops prompting.
    "assistant_confirm_timeout": 120,
    "assistant_auto_allow": {},
    # Vision needs a multimodal model. "auto" uses local Ollama when no Gemini key is configured.
    "assistant_vision_model": "qwen2.5vl:7b",
    "assistant_vision_backend": "auto",
    # Per-request routing overrides. "auto" keeps the chosen assistant_backend.
    "assistant_route_private": "local",
    "assistant_route_web": "gemini",
    "assistant_route_vision": "auto",
    "assistant_private_keywords": [
        "note", "notes", "password", "secret", "private", "calendar", "file", "document",
    ],
    # Absolute paths to search with the search_notes tool. Empty uses <data>/notes if it exists.
    "assistant_knowledge_dirs": [],
    # Memory: semantic recall uses Ollama embeddings when reachable, keywords otherwise.
    "assistant_embed_model": "nomic-embed-text",
    "assistant_memory_half_life_days": 30,
    "assistant_memory_compact_after": 40,
    "assistant_rag_enabled": True,
    # Send only the tools relevant to the question. 0 sends every tool.
    "assistant_tool_subset_size": 8,
    # Complex answers get one quiet verification pass before they are spoken.
    "assistant_verify_complex": True,
    # User plugins live here. Each Python file may call register(api) from plugin_sdk.
    "assistant_plugin_dir": "",
    # Optional Model Context Protocol servers, e.g. {"files": {"url": "http://host/mcp"}}.
    # Each server exposes its tools as mcp_<name>_<tool> through the plugin tool surface.
    "mcp_servers": {},
    "mcp_timeout": 10,
    # Allowlists for tools that can change things. Empty means those tools refuse to run.
    "assistant_apps": {},
    "assistant_commands": [],
    # Local dashboard / memory sizes.
    "assistant_history_limit": 50,
    "assistant_metrics_limit": 200,
    # Per-device preferences keyed by the id sent with requests. Supported keys: name, label,
    # model_choice, tts_length_scale, tts_style, tts_voice, tts_rate, tts_pitch, tts_volume.
    "assistant_devices": {},
    # Web tools: "auto" prefers a configured API, then falls back to DuckDuckGo HTML.
    "search_provider": "auto",
    "search_searxng_url": "",
    "search_allow_private": False,
    # Home Assistant REST integration. call_service is fail-closed on this allowlist.
    "home_assistant_url": "",
    "home_assistant_token": "",
    "home_assistant_services": [
        "light.turn_on", "light.turn_off", "switch.turn_on", "switch.turn_off",
        "scene.turn_on", "climate.set_temperature",
    ],
    # Optional CalDAV write-back for calendar_add. Read-only ICS remains the default source.
    "calendar_caldav_url": "",
    "calendar_caldav_user": "",
    "calendar_caldav_password": "",
    # MQTT transport for the robot vacuum tool. Leave blank until the device topic is known.
    "robot_enabled": False,
    "robot_broker": "",
    "robot_port": 8883,
    "robot_user": "",
    "robot_password": "",
    "robot_device_id": "",
    "robot_topic": "",
}


def _read() -> dict:
    if not os.path.exists(CONFIG) and os.path.exists(EXAMPLE):
        try:
            shutil.copyfile(EXAMPLE, CONFIG)
            print("config: created", CONFIG, "- set your location in it")
        except OSError as e:
            print("config: could not create config.json:", e)
    try:
        # utf-8-sig tolerates a BOM, which PowerShell's Set-Content -Encoding UTF8 adds.
        with open(CONFIG, encoding="utf-8-sig") as f:
            loaded = json.load(f)
        # A list, string or null is valid JSON but not a settings object; merging it would raise
        # TypeError at import and the agent would never start.
        if not isinstance(loaded, dict):
            print("config: %s is not a JSON object; using defaults" % os.path.basename(CONFIG))
            return dict(DEFAULT)
        return {**DEFAULT, **loaded}
    except FileNotFoundError:
        return dict(DEFAULT)
    except (OSError, json.JSONDecodeError) as e:
        print("config: %s is not readable (%s); using defaults" % (os.path.basename(CONFIG), e))
        return dict(DEFAULT)


VALUES = _read()


def get(name: str, fallback=None):
    v = VALUES.get(name, fallback)
    return fallback if v is None else v


def as_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Read a user-editable numeric setting without letting a typo abort startup."""
    try:
        value = int(get(name, default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def as_str(name: str, default: str = "") -> str:
    """Read a user-editable string setting, coercing stray numbers instead of crashing."""
    value = get(name, default)
    return default if value is None else str(value)


def set_value(name: str, value) -> None:
    """Update and persist one setting. Used by 'always allow' and device preferences."""
    VALUES[name] = value
    save()


def save() -> None:
    """Atomically write the in-memory settings back to config.json."""
    tmp = CONFIG + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(VALUES, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, CONFIG)
    except OSError as e:
        print("config: could not save %s (%s)" % (os.path.basename(CONFIG), e))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def location() -> tuple:
    """(latitude, longitude, timezone) or (None, None, tz) when it has not been set."""
    return VALUES.get("latitude"), VALUES.get("longitude"), get("timezone", "auto")


def has_location() -> bool:
    lat, lon, _tz = location()
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float))
