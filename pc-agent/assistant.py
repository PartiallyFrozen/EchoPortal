"""Jarvis assistant: a small hybrid LLM + tool loop for the EchoSpot.

The agent already owns the mic, the hub and the Windows desktop. This module adds the missing
piece: a reasoning loop that can call a few explicitly registered tools and return a short answer
suitable for a 480x480 round screen.

Backends (``assistant_backend`` in config.json):

  "gemini"  Gemini generateContent with function declarations
  "local"   an Ollama-compatible /api/chat endpoint (no cloud)
  "hybrid"  Gemini first, local fallback on any error (default)

Tools are injected by agent.py; this module never imports the hub or the timer store, so it stays
testable and cannot create an import cycle.
"""
import asyncio
import base64
import datetime as dt
import inspect
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zoneinfo

import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
GEMINI_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:streamGenerateContent?alt=sse"
DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"
DEFAULT_LOCAL_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_LOCAL_MODEL = "qwen3:14b"
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"  # local multimodal model for screenshots
MAX_TOOL_ROUNDS = 5
MAX_ANSWER_CHARS = 600
STREAM_INTERVAL = 0.10  # seconds between UI stream events; low enough to feel live

# The dashboard's model picker writes "assistant_model_choice". A preset id is either "hybrid" or
# "<backend>:<model>", so one setting selects both the backend and the exact model.
MODEL_PRESETS = [
    {"id": "hybrid", "label": "Hybrid · Gemini + local fallback", "backend": "hybrid", "model": ""},
    {"id": "gemini:" + DEFAULT_GEMINI_MODEL, "label": "Gemini Flash Lite · cloud",
     "backend": "gemini", "model": DEFAULT_GEMINI_MODEL},
    {"id": "local:" + DEFAULT_LOCAL_MODEL, "label": "Qwen 3 14B · local GPU",
     "backend": "local", "model": DEFAULT_LOCAL_MODEL},
]

SYSTEM_PROMPT = """You are Jarvis, a personal assistant running on the user's own Windows PC and shown on an Echo Spot with a 480x480 round screen.
Be brief and concrete: one or two short sentences, no markdown, no bullet lists unless asked.
Never emit voice/style labels, stage directions or raw JSON in the answer.
Available tools are declared with each request; use only those. Call a tool whenever the request
needs current, private or stored information, or any device action. Tools are your only source
of such facts.
Earlier chat turns are unverified background. Never repeat an earlier answer instead of using the
tool required for the current turn; trust tool results over previous replies.
Tool routing:
- Calendar, agenda, appointments or what is on today/tomorrow: `calendar`; calendar writes: `calendar_add`.
- Notes, documents, files, warranties or flight confirmations: `search_notes`.
- Buffalo Sabres schedule, date, opponent, venue, TV or jersey: `sabres_schedule`. Include the
  date/time, teams, venue, TV/status and jersey that the tool reports; do not drop those details.
- PC stats, CPU, GPU, VRAM or temperature: `pc_stats`.
- Timers, tasks, windows, media, screenshots, memory, web and Home Assistant have their matching
  tools; use them rather than guessing.
- If the user explicitly asks to search the web or fetch a URL, use `web_search` or `web_fetch`
  even when the topic also has a local tool.
- If no tool is available for a capability (the robot vacuum until MQTT is configured), say
  plainly that it is not configured and cannot be used. Never invent action names or emit raw
  tool identifiers.
Never claim that something was done, saved, cancelled, added, opened or played unless a tool
actually reported success; if you did not call a tool, say you cannot check. Prefer recalling a
remembered fact before asking the user to repeat it. Never invent file names, calendar events or
tool results. If a tool fails, say so plainly.
For state-changing tools, call the tool. The system then shows a confirmation request to the user;
do not ask for permission in your own reply and do not describe the action as already done before
it is confirmed.
"""

# Paired with the "jarvis" tts_style preset. It is a manner of speaking, not a character or an
# impression of any real person; the brief answers above still apply.
JARVIS_STYLE_PROMPT = """
Manner: calm, capable and understated, with dry good humour. Measured and precise, never gushing;
avoid exclamation marks. A brief "Certainly, sir" or "Of course" now and then is welcome, but do
not force it into every reply. Do not claim to be a fictional character.
"""


def _configured_now() -> dt.datetime:
    """Use the configured IANA zone when possible, otherwise the PC's local zone."""
    zone = str(config.get("timezone", "auto") or "auto").strip()
    if zone.lower() != "auto":
        try:
            return dt.datetime.now(zoneinfo.ZoneInfo(zone))
        except Exception:  # noqa: BLE001 - a bad zone must not break clock answers
            pass
    return dt.datetime.now().astimezone()


# Clock questions must never be answered by the model: past turns in the chat history can repeat
# an old time ("3:45 PM") even when the current time is in the system prompt.
_CLOCK_RE = re.compile(
    r"^\s*(?:"
    r"what(?:'s|\s+is)?\s+(?:the\s+)?(?:(?:current|today'?s)\s+)?(?:time|date|day)"
    r"(?:\s+is\s+it(?:\s+(?:right\s+now|now))?|\s+(?:right\s+now|now|today))?"
    r"|tell\s+me\s+(?:the\s+|today'?s\s+)?(?:time|date|day)"
    r"|(?:current|today'?s)\s+(?:time|date|day)"
    r")\s*\??\s*$",
    re.I,
)


def clock_answer(text: str) -> str:
    """Return an exact local time/date reply for a clock question, otherwise ''."""
    question = str(text or "")
    if not _CLOCK_RE.match(question):
        return ""
    now = _configured_now()
    low = question.lower()
    if "date" in low or ("day" in low and "today" in low):
        return "It's " + now.strftime("%A, %B %d, %Y") + "."
    if "day" in low:
        return "It's " + now.strftime("%A") + "."
    return "It's " + now.strftime("%I:%M %p").lstrip("0") + "."


# Set by the agent's HA poller so answers about the home are grounded without an extra tool round.
_HOME_CONTEXT = ""


def set_home_context(text: str) -> None:
    """Publish a short home snapshot for the next system prompt; blank disables it."""
    global _HOME_CONTEXT
    _HOME_CONTEXT = str(text or "")[:600]


def system_prompt(device: str = "") -> str:
    """The brief base prompt plus the optional voice style and live home snapshot."""
    base = SYSTEM_PROMPT + (JARVIS_STYLE_PROMPT if effective_tts_style(device) == "jarvis" else "")
    now = _configured_now()
    base += ("\n\nCurrent local time: "
             + now.strftime("%A, %B %d, %Y %I:%M %p %Z (UTC%z)")
             + ". Use this for any time or date question instead of guessing.")
    if _HOME_CONTEXT:
        base += "\n\nCurrent home state (from Home Assistant): " + _HOME_CONTEXT
    return base


# --------------------------------------------------------------------------- model selection
def _parse_model_choice(value: str) -> tuple[str, str]:
    """Return (backend, model) for a picker id, or ("", "") for hybrid/unknown values."""
    raw = str(value or "").strip()
    if not raw or raw.lower() == "hybrid" or ":" not in raw:
        return "", ""
    backend, model = raw.split(":", 1)
    backend, model = backend.strip().lower(), model.strip()
    if backend in ("gemini", "local") and model:
        return backend, model
    return "", ""


def _safe_model_name(value: str) -> bool:
    """Allow normal model tags but reject control characters and path tricks."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", str(value or "")))


def current_model_choice() -> str:
    """The persisted picker value, derived from the backend settings on older installs."""
    raw = str(config.get("assistant_model_choice", "") or "").strip()
    if raw:
        return raw
    backend = str(config.get("assistant_backend", "hybrid") or "hybrid").strip().lower()
    if backend == "gemini":
        model = str(config.get("assistant_gemini_model") or DEFAULT_GEMINI_MODEL).strip()
        return "gemini:" + model if model else "hybrid"
    if backend == "local":
        model = str(config.get("assistant_local_model") or DEFAULT_LOCAL_MODEL).strip()
        return "local:" + model if model else "hybrid"
    return "hybrid"


def _local_model_names() -> list[str]:
    """Ask Ollama which local models exist; an offline Ollama simply yields no extra options."""
    base = str(config.get("assistant_local_url") or DEFAULT_LOCAL_URL).rstrip("/")
    if base.endswith("/api/chat"):
        base = base[:-len("/api/chat")]
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=2.0) as resp:
            payload = json.load(resp)
        names = [str(m.get("name") or "").strip() for m in (payload.get("models") or [])
                 if isinstance(m, dict) and m.get("name")]
        return [name for name in names if _safe_model_name(name)]
    except Exception:  # noqa: BLE001 - local Ollama is optional
        return []


def available_model_choices() -> list[dict]:
    """Presets plus the user's configured and locally installed models."""
    choices = [dict(item) for item in MODEL_PRESETS]
    seen = {item["id"] for item in choices}

    def add(backend: str, model: str) -> None:
        model = str(model or "").strip()
        if not _safe_model_name(model):
            return
        option_id = backend + ":" + model
        if option_id in seen:
            return
        seen.add(option_id)
        suffix = " · local" if backend == "local" else " · cloud"
        choices.append({"id": option_id, "label": model + suffix,
                        "backend": backend, "model": model})

    add("gemini", config.get("assistant_gemini_model") or DEFAULT_GEMINI_MODEL)
    add("local", config.get("assistant_local_model") or DEFAULT_LOCAL_MODEL)
    for name in _local_model_names():
        add("local", name)
    return choices


def set_model_choice(value: str) -> dict:
    """Persist one picker choice. Only the three assistant settings are touched."""
    raw = str(value or "").strip()
    if not raw:
        return {"ok": False, "error": "model is required"}
    if raw.lower() == "hybrid":
        backend, model = "hybrid", ""
        option_id = "hybrid"
        label = "Hybrid · Gemini + local fallback"
    else:
        backend, model = _parse_model_choice(raw)
        if not backend or not _safe_model_name(model):
            return {"ok": False, "error": "unknown model"}
        option_id = backend + ":" + model
        label = model + (" · local" if backend == "local" else " · cloud")

    config.VALUES["assistant_model_choice"] = option_id
    config.VALUES["assistant_backend"] = backend
    if backend == "gemini":
        config.VALUES["assistant_gemini_model"] = model
    elif backend == "local":
        config.VALUES["assistant_local_model"] = model
    config.save()
    return {"ok": True, "id": option_id, "label": label, "backend": backend, "model": model}


# --------------------------------------------------------------------------- per-device settings
_DEVICE_KEYS = {
    "name", "label", "model_choice", "tts_length_scale", "tts_style", "tts_voice",
    "tts_rate", "tts_pitch", "tts_volume",
}


def _device_prefs(device: str = "") -> dict:
    """Return one device's saved preferences. Unknown ids simply get an empty dict."""
    key = str(device or "").strip()
    if not key:
        return {}
    devices = config.get("assistant_devices", {}) or {}
    if not isinstance(devices, dict):
        return {}
    prefs = devices.get(key, {})
    return dict(prefs) if isinstance(prefs, dict) else {}


def device_preferences(device: str = "") -> dict:
    """Public read helper used by the dashboard settings endpoint."""
    return _device_prefs(device)


def set_device_preferences(device: str, values: dict) -> dict:
    """Merge validated per-device settings and persist only assistant_devices."""
    key = str(device or "").strip()
    if not key:
        return {"ok": False, "error": "device is required"}
    updates = values if isinstance(values, dict) else {}
    cleaned = _device_prefs(key)
    for name, value in updates.items():
        if name not in _DEVICE_KEYS:
            continue
        if name == "model_choice":
            value = str(value or "").strip()
            if value and value.lower() != "hybrid":
                backend, model = _parse_model_choice(value)
                if not backend or not _safe_model_name(model):
                    return {"ok": False, "error": "unknown model"}
                value = backend + ":" + model
        elif name == "tts_length_scale":
            try:
                value = round(max(0.5, min(2.0, float(value))), 2)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid tts_length_scale"}
        elif name in ("tts_rate", "tts_pitch"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid " + name}
            value = max(-10, min(10, value)) if name == "tts_rate" else max(-8, min(8, value))
        elif name == "tts_volume":
            try:
                value = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid tts_volume"}
        elif name in ("name", "label", "tts_style", "tts_voice"):
            value = str(value or "")[:80]
        cleaned[name] = value
    devices = dict(config.get("assistant_devices", {}) or {})
    devices[key] = cleaned
    config.set_value("assistant_devices", devices)
    return {"ok": True, "device": key, **cleaned}


def effective_model_choice(device: str = "") -> str:
    """A device's model choice wins over the global picker."""
    return str(_device_prefs(device).get("model_choice") or "").strip() or current_model_choice()


def effective_tts_style(device: str = "") -> str:
    """A device's voice style wins over the global preset, including the wording prompt."""
    saved = _device_prefs(device).get("tts_style")
    value = saved if saved is not None else config.get("tts_style", "jarvis")
    return str(value or "jarvis").strip().lower()


def _keep_alive() -> str:
    """Validate Ollama's keep_alive value; bad user edits fall back instead of failing a turn."""
    value = config.get("assistant_keep_alive", "10m")
    if isinstance(value, (int, float)):
        return "%ds" % max(0, int(value))
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d+(\.\d+)?(ms|s|m|h)?", text) else "10m"


def _vision_model() -> str:
    """The local vision model; never used as a Gemini model id.

    Older installs may have an empty key, so fall back to the bundled default instead of
    leaving screenshot vision broken after an upgrade.
    """
    return str(config.get("assistant_vision_model", "") or "").strip() or DEFAULT_VISION_MODEL


# --------------------------------------------------------------------------- tool declarations
# ``confirm`` is local metadata; only name/description/parameters go to the model.
TOOLS = [
    {
        "name": "search_notes",
        "description": "Search the user's local notes and documents. Returns file paths and short snippets.",
        "parameters": {"query": "string"},
        "confirm": False,
    },
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns titles, URLs and snippets.",
        "parameters": {"query": "string"},
        "confirm": False,
    },
    {
        "name": "save_draft",
        "description": "Save a piece of writing to the PC's assistant_drafts folder. Returns the file name.",
        "parameters": {"filename": "string", "text": "string"},
        "confirm": False,
    },
    {
        "name": "timer",
        "description": "Set, cancel or list timers and alarms using natural language, e.g. 'set a timer for 10 minutes'.",
        "parameters": {"text": "string"},
        "confirm": False,
    },
    {
        "name": "calendar",
        "description": "List the next events from the user's calendars.",
        "parameters": {},
        "confirm": False,
    },
    {
        "name": "sabres_schedule",
        "description": "Look up the locally stored Buffalo Sabres 2026-27 schedule: dates, times, venues, opponents, TV and jerseys.",
        "parameters": {"query": "string"},
        "confirm": False,
    },
    {
        "name": "pc_stats",
        "description": "Read the PC's current CPU, memory, GPU and disk usage.",
        "parameters": {},
        "confirm": False,
    },
    {
        "name": "notify",
        "description": "Show a short banner on the Echo Spot.",
        "parameters": {"title": "string", "text": "string"},
        "confirm": False,
    },
    {
        "name": "type_text",
        "description": "Type text into the focused Windows window. Needs confirmation.",
        "parameters": {"text": "string", "enter": "boolean"},
        "confirm": True,
    },
    {
        "name": "open_app",
        "description": "Open an app from the configured allowlist. Needs confirmation.",
        "parameters": {"name": "string"},
        "confirm": True,
    },
    {
        "name": "run_command",
        "description": "Run a command from the configured allowlist. Needs confirmation.",
        "parameters": {"command": "string"},
        "confirm": True,
    },
    {
        "name": "robot",
        "description": "Send a command to the robot vacuum (start, stop, home, find). Needs confirmation.",
        "parameters": {"action": "string"},
        "confirm": True,
    },
    {
        "name": "remember",
        "description": "Save a durable fact about the user or their setup in local memory.",
        "parameters": {"text": "string"},
        "confirm": False,
    },
    {
        "name": "recall",
        "description": "Search persistent memory for facts relevant to a query.",
        "parameters": {"query": "string"},
        "confirm": False,
    },
    {
        "name": "forget",
        "description": "Remove matching facts from persistent memory. Needs confirmation.",
        "parameters": {"text": "string"},
        "confirm": True,
    },
    {
        "name": "web_fetch",
        "description": "Fetch a public web page and return its title and readable text.",
        "parameters": {"url": "string"},
        "confirm": False,
    },
    {
        "name": "look_at_screen",
        "description": "Capture the screen and let a vision model answer a question about it. Needs confirmation.",
        "parameters": {"question": "string"},
        "confirm": True,
    },
    {
        "name": "ha_state",
        "description": "Read entity states from a configured Home Assistant server.",
        "parameters": {"entity_id": "string"},
        "confirm": False,
    },
    {
        "name": "ha_call",
        "description": "Call an allowlisted Home Assistant service. Needs confirmation.",
        "parameters": {"domain": "string", "service": "string", "entity_id": "string", "data": "string"},
        "confirm": True,
    },
    {
        "name": "task_add",
        "description": "Add a task to the local assistant task list. Needs confirmation.",
        "parameters": {"text": "string"},
        "confirm": True,
    },
    {
        "name": "task_list",
        "description": "List open tasks from the local assistant task list.",
        "parameters": {},
        "confirm": False,
    },
    {
        "name": "calendar_add",
        "description": "Create a calendar event through configured CalDAV. Needs confirmation.",
        "parameters": {"title": "string", "start": "string", "duration_minutes": "string"},
        "confirm": True,
    },
    {
        "name": "window_list",
        "description": "List visible Windows window titles and process names.",
        "parameters": {},
        "confirm": False,
    },
    {
        "name": "window_focus",
        "description": "Bring a matching Windows window to the foreground. Needs confirmation.",
        "parameters": {"title": "string"},
        "confirm": True,
    },
    {
        "name": "window_close",
        "description": "Close a matching Windows window. Needs confirmation.",
        "parameters": {"title": "string"},
        "confirm": True,
    },
    {
        "name": "media_control",
        "description": "Control Windows media playback or volume. Needs confirmation.",
        "parameters": {"action": "string"},
        "confirm": True,
    },
    {
        "name": "undo",
        "description": "Reverse the last reversible action Jarvis performed, such as a saved "
                       "draft, task, remembered fact or forgotten fact. Say you cannot undo it "
                       "if there is nothing reversible.",
        "parameters": {},
        "confirm": False,
    },
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}
CONFIRM_TOOLS = {t["name"] for t in TOOLS if t["confirm"]}
WEB_TOOLS = {"web_search", "web_fetch"}
PRIVATE_TOOLS = {"search_notes", "recall", "calendar", "task_list"}


def _new_token() -> str:
    return uuid.uuid4().hex[:8]


def _http_error_body(error: urllib.error.HTTPError) -> str:
    """Read an Ollama error body once, falling back to the HTTP reason phrase."""
    try:
        return str(error.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - the body is diagnostic only
        return str(error)


def _is_repeat_limit_error(text: str) -> bool:
    """Ollama aborts a prediction that loops on repeated tokens."""
    low = str(text or "").casefold()
    return "repeat limit" in low or "token repeat" in low


def _unload_local_model(base: str, model: str) -> bool:
    """Ask Ollama to unload a model so its next request starts with a clean runner."""
    root = base[:-len("/api/chat")] if base.endswith("/api/chat") else base
    url = root if root.endswith("/api/generate") else root + "/api/generate"
    body = {"model": str(model or ""), "keep_alive": 0}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp.read(256)
        return True
    except Exception:  # noqa: BLE001 - unload is best effort; the caller re-raises on failure
        return False


def _tool_calls_message(calls: list[dict]) -> dict:
    """The assistant's own tool request, kept in the conversation for both backends."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": c.get("name"), "args": dict(c.get("args") or {})} for c in calls],
    }


def _tool_result_message(name: str, result: dict, images: list[str] | None = None) -> dict:
    """A tool answer. ``name`` and ``images`` are local metadata for the providers."""
    content = json.dumps(result, ensure_ascii=False)[:4000]
    msg = {"role": "tool", "name": str(name), "content": content}
    if images:
        msg["images"] = images
    return msg


def _call_signature(call: dict) -> str:
    return json.dumps({"name": call.get("name"), "args": call.get("args") or {}},
                      sort_keys=True, ensure_ascii=False)


_REQUIRED_TOOL_RE = re.compile(r"MUST call the `([a-z0-9_]+)` tool", re.IGNORECASE)


def required_tool(hint: str) -> str:
    """Extract the tool named by a routing hint so a missed call can be retried."""
    match = _REQUIRED_TOOL_RE.search(str(hint or ""))
    return match.group(1) if match else ""


def calls_from_text(text: str, available: set[str]) -> tuple[list[dict], str]:
    """Recover a tool call the model printed as JSON instead of using the tool channel.

    Qwen occasionally emits ``{"name": "timer", "arguments": {...}}`` as prose. Treat that as a
    call when the name is declared, so the user never sees raw action identifiers.
    """
    raw = str(text or "").strip()
    if not raw:
        return [], raw
    start = next((i for i, char in enumerate(raw) if char in "{["), -1)
    if start < 0:
        return [], raw
    candidate = raw[start:]
    try:
        value, end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return [], raw
    items = value if isinstance(value, list) else [value]
    calls: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            return [], raw
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or item.get("tool") or fn.get("name")
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else None
        if args is None:
            args = item.get("args") if isinstance(item.get("args"), dict) else None
        if args is None:
            args = fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {}
        if name not in available or not isinstance(args, dict):
            return [], raw
        calls.append({"name": name, "args": dict(args)})
    tail = candidate[end:].strip(" \t\r\n.,;:")
    return calls, tail


# Tools safe to invoke with the question itself when the model ignores a read-only routing hint.
# State-changing tools are never synthesised: they still require an explicit model tool call.
READ_ONLY_AUTO_TOOLS = {
    "calendar", "ha_state", "pc_stats", "recall", "sabres_schedule", "search_notes",
    "task_list", "web_fetch", "web_search", "window_list",
}

# `remember` is the one state-changing tool that has no confirmation card: storing exactly what the
# user explicitly asked to remember is safe to recover if the model skips the declared tool twice.
DIRECT_FACT_TOOLS = {"remember"}


def synthetic_args(name: str, question: str) -> dict | None:
    """Build minimal arguments for a tool the model failed to call after a retry."""
    text = str(question or "").strip()
    if name not in READ_ONLY_AUTO_TOOLS and name not in DIRECT_FACT_TOOLS:
        return None
    if name == "remember":
        match = re.match(r"^(?:please\s+)?remember\s+(?:that\s+)?(.+?)\s*[.]?\s*$",
                         text, re.IGNORECASE)
        return {"text": match.group(1).strip()} if match and match.group(1).strip() else None
    if name == "web_fetch":
        match = re.search(r"https?://\S+", text)
        return {"url": match.group(0).rstrip(".,;:)]}")} if match else None
    if name in {"calendar", "pc_stats", "task_list", "window_list"}:
        return {}
    return {"query": text} if text else None


def _confirm_audio(text: str) -> str | None:
    """Speak the approval prompt on the Spot if SAPI is available."""
    try:
        from tts import speak  # noqa: PLC0415 - optional at runtime
        return speak(text)
    except Exception:  # noqa: BLE001
        return None


def _confirm_timeout() -> int:
    try:
        return max(1, min(3600, int(config.get("assistant_confirm_timeout", 120))))
    except (TypeError, ValueError):
        return 120


def _schema(name: str, spec: dict | None = None) -> dict:
    spec = spec or TOOL_BY_NAME[name]
    properties = {k: {"type": "boolean" if v == "boolean" else "string"} for k, v in spec["parameters"].items()}
    return {
        "name": name,
        "description": spec["description"],
        "parameters": {"type": "object", "properties": properties, "required": list(properties)},
    }


# Hints for picking a small, relevant set of tool declarations instead of sending all of them.
_TOOL_KEYWORDS = {
    "search_notes": ("note", "notes", "document", "file", "warranty", "tax", "flight"),
    "web_search": ("search", "web", "look up", "news", "latest", "find", "schedule"),
    "web_fetch": ("fetch", "url", "http://", "https://", "read this page"),
    "save_draft": ("draft", "write", "save", "note down"),
    "timer": ("timer", "countdown", "remind me in", "pause timer", "cancel timer"),
    "calendar": ("calendar", "agenda", "appointment", "today", "tomorrow", "schedule"),
    "sabres_schedule": ("sabres", "buffalo sabres", "jersey", "uniform", "goathead", "remix",
                        "sabres schedule", "hockey schedule"),
    "pc_stats": ("pc", "cpu", "gpu", "vram", "memory usage", "uptime", "temperature"),
    "notify": ("notify", "notification", "alert me"),
    "type_text": ("type", "keyboard"),
    "open_app": ("open", "launch", "start notepad"),
    "run_command": ("command", "terminal", "powershell", "cmd"),
    "robot": ("robot", "vacuum", "clean"),
    "remember": ("remember", "store this", "keep in mind"),
    "recall": ("recall", "what did i", "do i know", "remind me what", "what is my", "where is my",
               "what do i", "anything you know", "what was my"),
    "forget": ("forget", "delete that fact", "stop remembering"),
    "look_at_screen": ("screen", "screenshot", "on my display", "what app is open", "read the screen"),
    "ha_state": ("home assistant", "state of", "is the light", "sensor"),
    "ha_call": ("turn on", "turn off", "thermostat", "scene", "home assistant"),
    "task_add": ("add a task", "to-do", "todo", "task to"),
    "task_list": ("tasks", "to-do list", "show my tasks"),
    "calendar_add": ("add an event", "calendar event", "put on my calendar"),
    "window_list": ("windows", "open windows"),
    "window_focus": ("focus", "bring to front", "switch to"),
    "window_close": ("close", "quit the window"),
    "media_control": ("play", "pause", "next track", "volume", "media"),
    "undo": ("undo", "revert", "take that back"),
}


def relevant_tool_names(question: str, messages: list[dict], available: set[str]) -> set[str]:
    """Pick a compact tool set while keeping tools already used in this turn available."""
    names = {str(n) for n in available}
    # Plugin tools have no keyword map; always expose them so they remain callable.
    extra = {n for n in names if n not in TOOL_BY_NAME}
    try:
        size = int(config.get("assistant_tool_subset_size", 8) or 0)
    except (TypeError, ValueError):
        size = 8
    if size <= 0 or len(names) <= size:
        return names
    used = {str(m.get("name")) for m in messages if m.get("role") == "tool" and m.get("name")}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            used.update(str(c.get("name")) for c in m["tool_calls"] if c.get("name"))
    text = ("%s %s" % (question, " ".join(str(m.get("content") or "") for m in messages[-4:]))).lower()
    scored = []
    for name in names - used - extra:
        score = sum(1 for word in _TOOL_KEYWORDS.get(name, ()) if word in text)
        if score:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], sorted(names).index(item[1])))
    return used | extra | {name for _score, name in scored[:max(0, size - len(used) - len(extra))]}


def _must_call(name: str) -> str:
    return ("For this turn you MUST call the `%s` tool before answering. Ignore earlier answers "
            "and do not answer from memory." % name)


def routing_hint(question: str, available: set[str]) -> str:
    """Turn-level system hint for high-confidence intents.

    The local model can anchor on an earlier wrong reply even when the right tool is declared.
    A short, explicit instruction placed after history keeps read-only and confirmation turns
    deterministic without hiding the conversation from the model.
    """
    low = str(question or "").casefold().strip()
    if not low:
        return ""

    if ("robot" in low or "vacuum" in low) and "robot" not in available:
        return ("No tool is available for this request. Say plainly that the robot vacuum is not "
                "configured and cannot be used; never emit action identifiers.")

    def wants(name: str, words: tuple[str, ...]) -> bool:
        return name in available and any(word in low for word in words)

    # Explicit web requests win over topical tools ("search the web for Sabres ...").
    if ("search the web" in low or "look up" in low or "latest news" in low
            or "find online" in low or "on the internet" in low):
        if "web_search" in available:
            return _must_call("web_search")
    if (("fetch " in low and ("http://" in low or "https://" in low))
            or "read this page" in low):
        if "web_fetch" in available:
            return _must_call("web_fetch")

    if wants("search_notes", ("note", "notes", "document", "warranty", "flight confirmation")):
        return _must_call("search_notes")
    if wants("sabres_schedule", ("sabres", "jersey", "goathead", "hometown remix")):
        return _must_call("sabres_schedule")

    if wants("calendar_add", ("add an event", "put on my calendar", "calendar event")):
        return _must_call("calendar_add")
    if wants("calendar", ("calendar", "agenda", "appointment")):
        return _must_call("calendar")

    if wants("timer", ("timer", "countdown", "remind me in")):
        return _must_call("timer")
    if wants("remember", ("remember", "store this", "keep in mind")):
        return _must_call("remember")
    if wants("forget", ("forget", "stop remembering", "delete that fact")):
        return _must_call("forget")
    if wants("recall", ("recall", "what did i", "what was my", "do i know", "anything you know",
                        "remind me what")) or (
            "recall" in available and "what is my" in low
            and not any(word in low for word in ("flight", "next game", "calendar"))
    ):
        return _must_call("recall")

    if wants("task_add", ("add a task", "to-do", "todo", "task to")):
        return _must_call("task_add")
    if wants("task_list", ("tasks", "to-do list")):
        return _must_call("task_list")
    if wants("pc_stats", ("vram", "cpu", "gpu", "uptime", "temperature", "my pc")):
        return _must_call("pc_stats")

    if wants("look_at_screen", ("screen", "screenshot", "on my display")):
        return _must_call("look_at_screen")
    if wants("ha_state", ("state of", "is the light", "sensor")):
        return _must_call("ha_state")
    if wants("ha_call", ("turn on", "turn off", "thermostat", "scene")):
        return _must_call("ha_call")

    if wants("window_list", ("open windows", "list my windows")):
        return _must_call("window_list")
    if wants("window_close", ("close the", "close window", "quit the window")):
        return _must_call("window_close")
    if wants("window_focus", ("focus", "bring to front", "switch to")):
        return _must_call("window_focus")
    if wants("media_control", ("play", "pause", "next track", "volume", "media")):
        return _must_call("media_control")
    if wants("open_app", ("open ", "launch")):
        return _must_call("open_app")
    if wants("type_text", ("type ", "keyboard")):
        return _must_call("type_text")
    if wants("run_command", ("command", "terminal", "powershell", "cmd")):
        return _must_call("run_command")
    if wants("undo", ("undo", "revert", "take that back")):
        return _must_call("undo")
    return ""


def _tool_schemas(messages: list[dict], question: str = "", available: set[str] | None = None,
                  plugin_specs: dict | None = None) -> list[dict]:
    """Declarations for this turn only, keeping the order stable for cache-friendly clients."""
    choices = set(available if available is not None else TOOL_BY_NAME)
    names = relevant_tool_names(question, messages or [], choices)
    specs = dict(TOOL_BY_NAME)
    if plugin_specs:
        specs.update(plugin_specs)
    return [_schema(name, specs[name]) for name in sorted(names) if name in specs]


def _validate_call(call: dict, callables: dict | None = None,
                   specs: dict | None = None) -> tuple[dict | None, str | None]:
    """Coerce and check model-supplied arguments against the tool's real callable signature."""
    name = str(call.get("name") or "")
    spec = (specs or TOOL_BY_NAME).get(name)
    if spec is None:
        return None, "unknown tool %s" % name
    args = call.get("args") or {}
    if not isinstance(args, dict):
        return None, "%s arguments must be an object" % name
    fn = (callables or {}).get(name)
    allowed = set(spec["parameters"])
    cleaned = dict(args)
    if fn is not None:
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            allowed = set()
            for key, param in signature.parameters.items():
                if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                allowed.add(key)
            for key in cleaned:
                if key not in allowed:
                    return None, "%s does not accept '%s'" % (name, key)
            for key, param in signature.parameters.items():
                if key not in cleaned and param.default is inspect.Parameter.empty and param.kind in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY
                ):
                    return None, "%s requires '%s'" % (name, key)
                if key not in cleaned or param.default is inspect.Parameter.empty:
                    continue
                expected = type(param.default)
                value = cleaned[key]
                if expected is bool and not isinstance(value, bool):
                    if str(value).strip().lower() in ("true", "1", "yes"):
                        cleaned[key] = True
                    elif str(value).strip().lower() in ("false", "0", "no"):
                        cleaned[key] = False
                    else:
                        return None, "%s.%s must be true or false" % (name, key)
                elif expected in (int, float) and not isinstance(value, bool):
                    try:
                        cleaned[key] = expected(value)
                    except (TypeError, ValueError):
                        return None, "%s.%s must be a number" % (name, key)
    for key, value in list(cleaned.items()):
        if key not in spec["parameters"] and fn is None:
            return None, "%s does not accept '%s'" % (name, key)
        if spec["parameters"].get(key) == "boolean" and isinstance(value, str):
            if value.strip().lower() in ("true", "1", "yes"):
                cleaned[key] = True
            elif value.strip().lower() in ("false", "0", "no"):
                cleaned[key] = False
        elif spec["parameters"].get(key) == "string" and isinstance(value, (dict, list)):
            cleaned[key] = json.dumps(value, ensure_ascii=False)[:2000]
    return {"name": name, "args": cleaned}, None


_SENTENCE_END = re.compile(r'[.!?…]["\'\)\]]?(?=\s|$)')
_SENTENCE_ABBREV = re.compile(r'\b(?:Mr|Mrs|Ms|Dr|St|Prof|vs|etc|e\.g|i\.e)\.$', re.I)


def complete_sentences(text: str, start: int = 0) -> tuple[list[str], int]:
    """Return newly completed sentences and the new scan offset. Pure and streaming-safe."""
    value = str(text or "")
    position = max(0, int(start))
    found: list[str] = []
    for match in _SENTENCE_END.finditer(value, position):
        piece = value[position:match.end()].strip()
        if len(piece) < 8 or _SENTENCE_ABBREV.search(piece):
            continue
        found.append(piece)
        position = match.end()
    return found, position


def pause_markers(text: str) -> tuple[str, int]:
    """Strip spoken pause markers from visible text and count them."""
    marker = str(config.get("assistant_pause_marker", "[pause]") or "").strip()
    value = str(text or "")
    if not marker or marker not in value:
        return value, 0
    return " ".join(value.replace(marker, " ").split()), value.count(marker)


def _is_complex(question: str, messages: list[dict]) -> bool:
    text = str(question or "").lower()
    if len(text) > 180 or sum(1 for m in messages if m.get("role") == "tool") >= 2:
        return True
    return any(word in text for word in ("compare", "versus", "plan ", "should i", "why ", "explain", "summar"))


# --------------------------------------------------------------------------- small tools
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "build", "dist", "_archive", "library", "media"}
TEXT_EXT = {".md", ".txt", ".rst", ".py", ".kt", ".json", ".html", ".js", ".css", ".yml", ".yaml"}


def _knowledge_dirs() -> list[str]:
    configured = config.get("assistant_knowledge_dirs", []) or []
    if not isinstance(configured, list):
        configured = [configured]
    dirs = [os.path.abspath(os.path.expanduser(str(p).strip())) for p in configured if str(p).strip()]
    if not dirs:
        fallback = config.data_path("notes")
        if os.path.isdir(fallback):
            dirs = [fallback]
    return [d for d in dirs if os.path.isdir(d)]


def _note_chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    """Small overlapping windows so long documents can be matched and quoted precisely."""
    value = " ".join(str(text or "").split())
    if len(value) <= size:
        return [value] if value else []
    result, start = [], 0
    while start < len(value):
        result.append(value[start:start + size])
        if start + size >= len(value):
            break
        start += size - overlap
    return result


def search_notes(query: str, limit: int = 5) -> list[dict]:
    """Search configured folders with keywords plus local embeddings when available."""
    words = [w.lower() for w in re.findall(r"\w+", str(query)) if len(w) > 2]
    if not words:
        return []
    candidates: list[tuple[int, str, str]] = []
    files = 0
    for root in _knowledge_dirs():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(path) > 256 * 1024:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                files += 1
                if files > 500:
                    break
                low = text.lower()
                keyword = sum(low.count(w) for w in words)
                if keyword:
                    at = min(low.find(w) for w in words if low.find(w) >= 0)
                    snippet = " ".join(text[max(0, at - 110):at + 220].split())
                    candidates.append((keyword, path, snippet[:320]))
            if files > 500:
                break
    if not candidates:
        return []
    if config.get("assistant_rag_enabled", True):
        try:
            import memory  # noqa: PLC0415 - optional local embedding backend

            chunk_groups = [_note_chunks(snippet) for _keyword, _path, snippet in candidates]
            scores = memory.semantic_scores(str(query), [c for group in chunk_groups for c in group])
            ranked, offset = [], 0
            for (keyword, path, snippet), group in zip(candidates, chunk_groups):
                best = max(scores[offset:offset + len(group)] or [0.0])
                offset += len(group)
                if keyword <= 0 and best < 0.55:
                    continue
                ranked.append((keyword * 3 + best * 4, path, snippet))
            scored = [(round(n, 2), p, s) for n, p, s in ranked]
            if scored:
                scored.sort(key=lambda h: h[0], reverse=True)
                return [{"path": p, "snippet": s, "score": n} for n, p, s in scored[:limit]]
        except Exception:  # noqa: BLE001 - keyword results remain the fallback
            pass
    candidates.sort(key=lambda h: h[0], reverse=True)
    return [{"path": p, "snippet": s, "score": n} for n, p, s in candidates[:limit]]


def web_search(query: str, limit: int = 5) -> list[dict]:
    """Provider-backed web search; falls back inside search_providers if needed."""
    from search_providers import search  # noqa: PLC0415 - optional module
    return search(str(query), limit)


def web_fetch(url: str) -> dict:
    """Fetch a public URL with an SSRF guard."""
    from search_providers import fetch_url  # noqa: PLC0415 - optional module
    return fetch_url(str(url))


def _safe_filename(name: str) -> str:
    base = os.path.basename(str(name)).strip()
    base = re.sub(r"[^A-Za-z0-9 ._-]", "-", base).strip(" .") or "draft"
    if not base.lower().endswith(".md"):
        base += ".md"
    return base[:100]


def save_draft(filename: str, text: str) -> dict:
    folder = config.data_path("assistant_drafts")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _safe_filename(filename))
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(text))
    return {"ok": True, "file": os.path.basename(path), "path": path}


def delete_draft(path: str) -> dict:
    """Undo helper: remove only a draft written by save_draft, inside the drafts folder."""
    folder = os.path.abspath(config.data_path("assistant_drafts"))
    target = os.path.abspath(str(path or ""))
    if os.path.dirname(target) != folder or not target.endswith(".md"):
        return {"ok": False, "error": "draft is outside the drafts folder"}
    try:
        os.remove(target)
    except FileNotFoundError:
        return {"ok": True, "deleted": 0}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "deleted": 1}


def remove_task_line(task: str) -> dict:
    """Undo helper: remove the matching unchecked task line, leaving all other lines alone."""
    path = _tasks_path()
    wanted = " ".join(str(task).split())[:300]
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {"ok": True, "deleted": 0}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    kept, deleted = [], 0
    for line in lines:
        if deleted == 0 and line.startswith("- [ ] ") and " ".join(line[6:].split()) == wanted:
            deleted += 1
        else:
            kept.append(line)
    if deleted:
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.writelines(kept)
        except OSError as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "deleted": deleted}


def delete_calendar_event(uid: str) -> dict:
    """Undo helper: delete the exact CalDAV object created by calendar_add."""
    collection = str(config.get("calendar_caldav_url", "") or "").strip()
    key = str(uid or "")
    if not collection or not re.fullmatch(r"[A-Za-z0-9@._-]{1,120}", key):
        return {"ok": False, "error": "invalid calendar event"}
    headers = {"If-Match": "*"}
    user = str(config.get("calendar_caldav_user", "") or "")
    if user:
        token = base64.b64encode(("%s:%s" % (user, config.get("calendar_caldav_password", "") or "")).encode()).decode()
        headers["Authorization"] = "Basic " + token
    try:
        req = urllib.request.Request(collection.rstrip("/") + "/" + key + ".ics",
                                     headers=headers, method="DELETE")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = getattr(resp, "status", 204)
        return {"ok": 200 <= status < 300, "status": status}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}


def open_app(name: str) -> dict:
    apps = config.get("assistant_apps", {}) or {}
    target = apps.get(str(name)) if isinstance(apps, dict) else None
    if not target:
        return {"ok": False, "error": "app is not in assistant_apps"}
    try:
        subprocess.Popen([str(target)], shell=False)
        return {"ok": True, "app": str(name)}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def run_command(command: str) -> dict:
    allowed = [str(c) for c in (config.get("assistant_commands", []) or [])]
    cmd = str(command).strip()
    if cmd not in allowed:
        return {"ok": False, "error": "command is not in assistant_commands"}
    try:
        p = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=30)
        return {"ok": p.returncode == 0, "stdout": p.stdout.strip()[:1000], "stderr": p.stderr.strip()[:500]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}


def _tasks_path() -> str:
    return config.data_path("assistant_tasks.md")


def task_add(text: str) -> dict:
    item = " ".join(str(text).split())[:300]
    if not item:
        return {"ok": False, "error": "task text is empty"}
    path = _tasks_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("- [ ] %s\n" % item)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "task": item, "file": os.path.basename(path)}


def task_list() -> dict:
    path = _tasks_path()
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {"ok": True, "tasks": [], "count": 0}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    tasks = [" ".join(line[6:].split()) for line in lines if line.startswith("- [ ] ")]
    return {"ok": True, "tasks": tasks[:50], "count": len(tasks)}


def _ics_escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n")[:200])


def calendar_add(title: str, start: str, duration_minutes: str = "30") -> dict:
    """Write one VEVENT to a configured CalDAV collection. Read-only ICS stays the default."""
    collection = str(config.get("calendar_caldav_url", "") or "").strip()
    if not collection:
        return {"ok": False, "error": "calendar_caldav_url is not configured"}
    try:
        when = dt.datetime.fromisoformat(str(start).strip().replace("Z", "+00:00"))
        minutes = max(1, min(24 * 60, int(str(duration_minutes).strip() or 30)))
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": "invalid start or duration: %s" % e}
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    when = when.astimezone(dt.timezone.utc)
    end = when + dt.timedelta(minutes=minutes)
    stamp = lambda d: d.strftime("%Y%m%dT%H%M%SZ")  # noqa: E731 - tiny local formatter
    uid = uuid.uuid4().hex + "@echoportal"
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EchoPortal//Jarvis//EN", "BEGIN:VEVENT",
        "UID:" + uid, "DTSTAMP:" + stamp(dt.datetime.now(dt.timezone.utc)),
        "DTSTART:" + stamp(when), "DTEND:" + stamp(end),
        "SUMMARY:" + _ics_escape(title), "END:VEVENT", "END:VCALENDAR", "",
    ])
    url = collection.rstrip("/") + "/" + uid + ".ics"
    headers = {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"}
    user = str(config.get("calendar_caldav_user", "") or "")
    if user:
        token = base64.b64encode(("%s:%s" % (user, config.get("calendar_caldav_password", "") or "")).encode()).decode()
        headers["Authorization"] = "Basic " + token
    try:
        req = urllib.request.Request(url, data=ics.encode("utf-8"), headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = getattr(resp, "status", 200)
        return {"ok": 200 <= status < 300, "status": status, "uid": uid, "start": when.isoformat(), "end": end.isoformat()}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _local_vision_answer(image: str, question: str) -> str:
    """Ask the local Ollama vision model about one screenshot."""
    base = str(config.get("assistant_local_url") or DEFAULT_LOCAL_URL).rstrip("/")
    url = base if base.endswith("/api/chat") else base + "/api/chat"
    body = {"model": _vision_model(), "stream": False,
            "messages": [{"role": "user", "content": question, "images": [image]}]}
    payload = _post_json(url, body, {"Content-Type": "application/json"}, 90.0)
    return str((payload.get("message") or {}).get("content") or "").strip()


def look_at_screen(question: str = "What is on the screen?") -> dict:
    """Capture the desktop and ask the configured vision backend about it.

    The screenshot is returned as ``image`` so the engine can attach it to the tool result too;
    that lets the main model see the same pixels when it writes the final answer.
    """
    try:
        from screenshot import capture  # noqa: PLC0415 - optional at runtime
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "screenshot unavailable: %s" % e}
    shot = capture()
    if not shot.get("ok"):
        return shot
    image = str(shot.get("image") or "")
    if not image:
        return {"ok": False, "error": "empty screenshot"}
    q = " ".join(str(question or "What is on the screen?").split())[:600]
    backend = str(config.get("assistant_vision_backend", "auto") or "auto").lower()
    key = (os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key", "") or "").strip()
    fallback = ""
    try:
        if backend == "local" or (backend == "auto" and not key):
            answer = _local_vision_answer(image, q)
        else:
            if not key:
                answer = _local_vision_answer(image, q)
                fallback = "local"
            else:
                # assistant_vision_model is a local Ollama tag; Gemini needs its own model id.
                model = str(config.get("assistant_gemini_model") or DEFAULT_GEMINI_MODEL).strip()
                body = {
                    "contents": [{"role": "user", "parts": [
                        {"text": q}, {"inline_data": {"mime_type": "image/jpeg", "data": image}},
                    ]}],
                    "generationConfig": {"temperature": 0.2},
                    "systemInstruction": {"parts": [{
                        "text": "Answer briefly about the screenshot. Never guess text you cannot read.",
                    }]},
                }
                try:
                    payload = _post_json(GEMINI_URL % model, body,
                                         {"Content-Type": "application/json", "x-goog-api-key": key},
                                         45.0)
                    candidates = payload.get("candidates") or []
                    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
                    answer = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
                except Exception:  # noqa: BLE001 - offline vision is still available
                    answer = _local_vision_answer(image, q)
                    fallback = "local"
                if not answer:
                    answer = _local_vision_answer(image, q)
                    fallback = "local"
        out = {"ok": True, "answer": answer[:MAX_ANSWER_CHARS] or "I couldn't read the screen.",
               "window_title": shot.get("window_title") or "", "image": image}
        if fallback:
            out["fallback"] = fallback
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- engine
class AssistantEngine:
    """Runs the model/tool loop and remembers the last few turns in memory."""

    def __init__(self, tools: dict, broadcast=None, store=None):
        self.tools = dict(tools)
        self.tool_specs: dict[str, dict] = {}
        self.broadcast = broadcast
        self.store = store
        self.metrics: list[dict] = []
        self.cancelled: set[str] = set()
        self.cancel_all = False
        self._async_loop = None
        self.history: list[dict] = []
        self.pending: dict[str, dict] = {}
        self.undo_log: dict[str, list[dict]] = {}
        self.tool_metrics: list[dict] = []
        self._verified: set[str] = set()
        self.dry_run = False
        self._current_device = ""
        self.turn_trace: list[str] = []
        self._plugin_hooks: dict[str, list] = {}
        self._load_plugins()
        self._reload_history()

    def _load_plugins(self) -> None:
        """Load optional Python plugins; one bad file never stops the agent."""
        folder = str(config.get("assistant_plugin_dir", "") or "").strip()
        if not folder:
            folder = config.data_path("plugins")
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith(".py") and not n.startswith("."))
        except OSError:
            names = []
        import plugin_sdk  # noqa: PLC0415 - optional extension surface

        api = plugin_sdk.PluginAPI(self)
        for name in names:
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                namespace = {"__name__": "jarvis_plugin_" + name[:-3], "__file__": path}
                exec(compile(source, path, "exec"), namespace)  # noqa: S102 - explicit user plugin
                register = namespace.get("register")
                if callable(register):
                    register(api)
            except Exception as e:  # noqa: BLE001
                print("assistant plugin %s skipped: %s" % (name, e))
        # Optional MCP servers use the same registration surface as Python plugins. An unreachable
        # or misconfigured server is logged by the provider and never stops the agent.
        try:
            import mcp_client  # noqa: PLC0415 - optional extension surface
            mcp_client.register_mcp_tools(api)
        except Exception as e:  # noqa: BLE001
            print("MCP tools skipped: %s" % e)

    def _plugin_emit(self, op: str, data: dict) -> None:
        for fn in list(self._plugin_hooks.get(str(op).lower(), [])):
            try:
                fn(dict(data))
            except Exception:  # noqa: BLE001
                pass

    def _reload_history(self) -> None:
        """Rebuild the short model context from the store after a delete/clear."""
        self.history = []
        if self.store is None:
            return
        summary = ""
        try:
            summary = str(getattr(self.store, "latest_summary", lambda: "")() or "")
        except Exception:  # noqa: BLE001 - a simple fake store may not implement summaries
            summary = ""
        if summary:
            self.history.append({"role": "system", "content": "Earlier conversation: " + summary[:2000]})
        # The store returns rows like {"user": ..., "assistant": ...}; the model needs chat
        # messages. Convert once here so every later request has the right shape.
        for turn in self.store.recent_turns(limit=6):
            self.history.append({"role": "user", "content": str(turn.get("user") or "")})
            self.history.append({"role": "assistant", "content": str(turn.get("assistant") or "")})

    def _backend(self) -> str:
        choice = str(config.get("assistant_backend", "hybrid") or "hybrid").lower()
        return choice if choice in ("gemini", "local", "hybrid") else "hybrid"

    def _key(self) -> str:
        return (os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key", "") or "").strip()

    def _gemini_model(self, device: str = "") -> str:
        """Gemini ids come only from the picker/config, never from the local vision model."""
        selected_backend, selected_model = _parse_model_choice(effective_model_choice(device))
        if selected_backend == "gemini" and selected_model:
            return selected_model
        return str(config.get("assistant_gemini_model") or DEFAULT_GEMINI_MODEL).strip()

    def _model(self, vision: bool = False, device: str = "") -> str:
        """Compatibility wrapper for older callers; local vision has its own helper."""
        return self._gemini_model(device)

    @staticmethod
    def _payload_parts(payload: dict) -> list:
        candidates = payload.get("candidates") or []
        if not candidates:
            return []
        return ((candidates[0].get("content") or {}).get("parts") or [])

    def _route(self, messages: list[dict], question: str = "", device: str = "") -> str:
        """Pick a backend for this turn. 'auto' rules fall back to the configured backend."""
        choice = self._backend()
        selected_backend, _ = _parse_model_choice(effective_model_choice(device))
        tool_names = {m.get("name") for m in messages if m.get("role") == "tool"}
        if any(m.get("images") for m in messages):
            # Vision keeps its own route: the default local model is text-only.
            rule = config.get("assistant_route_vision", choice)
        elif selected_backend:
            # An explicit picker choice wins over per-topic routing.
            rule = selected_backend
        elif tool_names & WEB_TOOLS:
            rule = config.get("assistant_route_web", choice)
        else:
            words = str(question).lower()
            keys = config.get("assistant_private_keywords", []) or []
            private = tool_names & PRIVATE_TOOLS or any(str(k).lower() in words for k in keys)
            rule = config.get("assistant_route_private", choice) if private else choice
        rule = str(rule or choice).lower()
        if rule == "auto":
            rule = choice
        return rule if rule in ("gemini", "local", "hybrid") else choice

    def cancel(self, token: str = "") -> bool:
        """Cancel one in-flight turn by token, or every turn when no token is given."""
        key = str(token or "")
        if not key:
            self.cancel_all = True
            self.pending.clear()
            return True
        self.cancelled.add(key)
        if key in self.pending:
            self.pending.pop(key, None)
            return True
        return True

    def is_cancelled(self, token: str) -> bool:
        return self.cancel_all or str(token) in self.cancelled

    def metrics_snapshot(self, limit: int = 50) -> list[dict]:
        try:
            count = max(1, min(1000, int(limit)))
        except (TypeError, ValueError):
            count = 50
        return [dict(m) for m in self.metrics[-count:]]

    def history_snapshot(self, limit: int = 50) -> list[dict]:
        try:
            count = max(1, min(1000, int(limit)))
        except (TypeError, ValueError):
            count = 50
        if self.store is not None:
            return [dict(t) for t in self.store.recent_turns(count)]
        return [dict(t) for t in self.history[-count:]]

    def delete_turn(self, turn_id: int) -> dict:
        """Delete one saved chat turn and refresh the short model context."""
        if self.store is None:
            return {"ok": True, "deleted": 0, "id": int(turn_id)}
        result = self.store.delete_turn(turn_id)
        self._reload_history()
        return result

    def clear_history(self) -> dict:
        """Delete every saved chat turn; remembered facts are deliberately untouched."""
        if self.store is None:
            self.history = []
            return {"ok": True, "deleted": 0}
        result = self.store.clear_turns()
        self._reload_history()
        return result

    def _emit_sync(self, emit, op: str, **data):
        """Thread-safe progress from a streaming HTTP reader."""
        event = {"op": op, **data}
        self._plugin_emit(op, event)
        target = emit or self.broadcast
        if target is None:
            return
        try:
            result = target(event)
            if inspect.isawaitable(result):
                loop = getattr(self, "_async_loop", None)
                if loop is not None and not loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(result, loop)
                    if op == "speak":
                        # Rendering a sentence clip must finish before the streaming reader moves
                        # on, otherwise the final reply can race ahead of its queued audio.
                        try:
                            future.result(timeout=60)
                        except Exception:  # noqa: BLE001 - audio is optional
                            pass
                else:
                    result.close()
        except Exception:  # noqa: BLE001
            pass

    def _post(self, url: str, body: dict, headers: dict, timeout: float) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    def _post_stream(self, url: str, body: dict, headers: dict, timeout: float,
                     token: str = "", emit=None, device: str = "") -> dict:
        """Read Ollama's NDJSON stream, emitting partial text as it arrives."""
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        chunks: list[str] = []
        raw_calls: list[dict] = []
        last = 0.0
        sentence_offset = 0
        pauses_seen = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if token and self.is_cancelled(token):
                    break
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = payload.get("message") or {}
                piece = str(msg.get("content") or "")
                if piece:
                    chunks.append(piece)
                    now = time.time()
                    if emit and now - last >= STREAM_INTERVAL:
                        last = now
                        self._emit_sync(emit, "stream", text="".join(chunks)[:MAX_ANSWER_CHARS],
                                        token=token, device=device)
                    if emit:
                        sentences, sentence_offset = complete_sentences("".join(chunks), sentence_offset)
                        for sentence in sentences:
                            self._emit_sync(emit, "speak", text=sentence[:MAX_ANSWER_CHARS],
                                            token=token, device=device)
                        _visible, pauses = pause_markers("".join(chunks))
                        if pauses > pauses_seen:
                            pauses_seen = pauses
                            self._emit_sync(emit, "pause", text=str(pauses), token=token, device=device)
                raw_calls.extend(msg.get("tool_calls") or [])
        full = "".join(chunks)
        trailing = full[sentence_offset:].strip()
        if emit and trailing:
            # A short answer without terminal punctuation would otherwise never be spoken
            # in streaming mode: there is no completed sentence to trigger a clip.
            self._emit_sync(emit, "speak", text=trailing[:MAX_ANSWER_CHARS],
                            token=token, device=device)
        return {"text": full.strip(), "raw_calls": raw_calls}

    def _post_sse(self, url: str, body: dict, headers: dict, timeout: float,
                  token: str = "", emit=None, device: str = "") -> dict:
        """Read Gemini's server-sent event stream and accumulate parts."""
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        payloads: list[dict] = []
        chunks: list[str] = []
        last = 0.0
        sentence_offset = 0
        pauses_seen = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if token and self.is_cancelled(token):
                    break
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                payloads.append(payload)
                parts = ((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                piece = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p)
                if piece:
                    chunks.append(piece)
                    now = time.time()
                    if emit and now - last >= STREAM_INTERVAL:
                        last = now
                        self._emit_sync(emit, "stream", text="".join(chunks)[:MAX_ANSWER_CHARS],
                                        token=token, device=device)
                    if emit:
                        sentences, sentence_offset = complete_sentences("".join(chunks), sentence_offset)
                        for sentence in sentences:
                            self._emit_sync(emit, "speak", text=sentence[:MAX_ANSWER_CHARS],
                                            token=token, device=device)
                        _visible, pauses = pause_markers("".join(chunks))
                        if pauses > pauses_seen:
                            pauses_seen = pauses
                            self._emit_sync(emit, "pause", text=str(pauses), token=token, device=device)
        full = "".join(chunks)
        trailing = full[sentence_offset:].strip()
        if emit and trailing:
            self._emit_sync(emit, "speak", text=trailing[:MAX_ANSWER_CHARS],
                            token=token, device=device)
        return {"payloads": payloads}

    @staticmethod
    def _merge_local_raw(raw_calls: list[dict]) -> list[dict]:
        """Coalesce partial tool-call chunks from a streamed Ollama response."""
        out: list[dict] = []
        for c in raw_calls or []:
            fn = c.get("function") or {}
            name, args = fn.get("name"), fn.get("arguments") or {}
            idx = c.get("index")
            target = out[idx] if isinstance(idx, int) and 0 <= idx < len(out) else None
            if target is None and name:
                target = next((x for x in out if (x.get("function") or {}).get("name") == name), None)
            if target is None:
                out.append({"function": {"name": name, "arguments": args}})
                continue
            if name:
                target["function"]["name"] = name
            old = target["function"].get("arguments") or {}
            if isinstance(old, str) and isinstance(args, str):
                target["function"]["arguments"] = args if args.startswith(old) else old + args
            elif isinstance(old, dict) and isinstance(args, dict):
                old.update(args)
            elif args:
                target["function"]["arguments"] = args
        return out

    @staticmethod
    def _parse_local_calls(raw_calls: list[dict]) -> list[dict]:
        calls = []
        for c in raw_calls or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append({"name": fn.get("name"), "args": dict(args)})
        return calls

    @staticmethod
    def _parts_result(parts: list, strip: bool = True) -> tuple[str, list[dict]]:
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p)
        calls = [{"name": p["functionCall"]["name"], "args": dict(p["functionCall"].get("args") or {})}
                 for p in parts if isinstance(p, dict) and "functionCall" in p]
        # A streaming delta often starts with the space that belongs between two words. Stripping
        # every delta would turn "VOICE" + " CHECK" into "VOICECHECK"; strip only the full answer.
        return (text.strip() if strip else text), calls

    @staticmethod
    def _gemini_response(content) -> dict:
        """Gemini's functionResponse.response must be an object, not a JSON string."""
        try:
            value = json.loads(str(content))
        except (TypeError, json.JSONDecodeError):
            value = content
        return value if isinstance(value, dict) else {"result": value}

    def _gemini(self, messages: list[dict], token: str = "", emit=None, device: str = "",
                stream: bool = False, question: str = "") -> dict:
        contents = []
        system = []
        for m in messages:
            if m["role"] == "system":
                system.append(str(m["content"]))
                continue
            if m["role"] == "tool":
                parts = [{"functionResponse": {
                    "name": str(m.get("name") or "tool"),
                    "response": self._gemini_response(m.get("content")),
                }}]
                for image in m.get("images") or []:
                    parts.append({"inline_data": {"mime_type": "image/jpeg", "data": str(image)}})
                contents.append({"role": "user", "parts": parts})
                continue
            if m["role"] == "assistant" and m.get("tool_calls"):
                parts = []
                if m.get("content"):
                    parts.append({"text": str(m["content"])})
                parts.extend({"functionCall": {"name": c.get("name"),
                                               "args": dict(c.get("args") or {})}}
                             for c in m["tool_calls"])
                contents.append({"role": "model", "parts": parts})
                continue
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(m["content"])}]})
        body = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
            "tools": [{"functionDeclarations": _tool_schemas(
                messages, question, set(self.tools), self.tool_specs)}],
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key()}
        if stream:
            result = self._post_sse(GEMINI_STREAM_URL % self._gemini_model(device), body, headers,
                                    45.0, token, emit, device)
            calls, pieces = [], []
            for payload in result["payloads"]:
                text, found = self._parts_result(self._payload_parts(payload), strip=False)
                if text:
                    pieces.append(text)
                calls.extend(found)
            return {"text": "".join(pieces).strip(), "calls": calls,
                    "_model": self._gemini_model(device)}
        payload = self._post(GEMINI_URL % self._gemini_model(device), body, headers, 45.0)
        text, calls = self._parts_result(self._payload_parts(payload))
        return {"text": text, "calls": calls, "_model": self._gemini_model(device)}

    def _local(self, messages: list[dict], token: str = "", emit=None, device: str = "",
               stream: bool = False, question: str = "", _retried: bool = False) -> dict:
        base = str(config.get("assistant_local_url") or DEFAULT_LOCAL_URL).rstrip("/")
        url = base if base.endswith("/api/chat") else base + "/api/chat"
        # Ollama requires the assistant tool_calls message and role:"tool" answers in the
        # conversation. Internal messages carry extra metadata that the API does not accept.
        wire_messages = []
        has_images = False
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                wire_messages.append({
                    "role": "assistant",
                    "content": str(m.get("content") or ""),
                    "tool_calls": [{"function": {"name": c.get("name"),
                                                 "arguments": dict(c.get("args") or {})}}
                                   for c in m["tool_calls"]],
                })
            elif m["role"] == "tool":
                item = {"role": "tool", "content": str(m.get("content") or "")}
                if m.get("images"):
                    has_images = True
                    item["images"] = [str(x) for x in m["images"]]
                wire_messages.append(item)
            else:
                item = {"role": m["role"], "content": str(m.get("content") or "")}
                if m.get("images"):
                    has_images = True
                    item["images"] = [str(x) for x in m["images"]]
                wire_messages.append(item)
        model = str(config.get("assistant_local_model") or DEFAULT_LOCAL_MODEL)
        selected_backend, selected_model = _parse_model_choice(effective_model_choice(device))
        if selected_backend == "local" and selected_model:
            model = selected_model
        if has_images:
            model = _vision_model() or model
        body = {
            "model": model,
            "messages": wire_messages,
            "tools": [{"type": "function", "function": schema}
                      for schema in _tool_schemas(messages, question, set(self.tools), self.tool_specs)],
            "stream": bool(stream),
            "keep_alive": _keep_alive(),
            # Thinking models (qwen3, mimo, ...) would otherwise stream reasoning into the
            # answer and feel slow. Ollama accepts think:false on plain models too.
            "think": False,
        }
        headers = {"Content-Type": "application/json"}
        try:
            if stream:
                result = self._post_stream(url, body, headers, 90.0, token, emit, device)
            else:
                payload = self._post(url, body, headers, 90.0)
        except urllib.error.HTTPError as error:
            if (_retried or not _is_repeat_limit_error(_http_error_body(error))
                    or not _unload_local_model(base, model)):
                raise
            # A long-lived Qwen runner occasionally gets stuck repeating tokens. Unloading it
            # clears the KV cache; retry once with a fresh runner before giving up on the turn.
            if stream and emit:
                self._emit_sync(emit, "stream_reset", token=token, device=device)
            return self._local(messages, token, emit, device, stream, question, _retried=True)
        if stream:
            calls = self._parse_local_calls(self._merge_local_raw(result["raw_calls"]))
            return {"text": result["text"], "calls": calls, "_model": model}
        msg = payload.get("message") or {}
        calls = self._parse_local_calls(msg.get("tool_calls") or [])
        return {"text": str(msg.get("content") or "").strip(), "calls": calls, "_model": model}

    def _model_errors(self) -> tuple:
        return (urllib.error.URLError, KeyError, IndexError, TypeError, TimeoutError,
                OSError, ValueError, RuntimeError)

    def _record_metric(self, backend: str, started: float, result: dict, device: str = "",
                       fallback: bool = False) -> None:
        now = time.time()
        calls = result.get("calls") or []
        self.metrics.append({
            "ts": int(now),
            "backend": backend,
            "model": str(result.get("_model") or ""),
            "fallback": bool(fallback),
            "device": str(device or ""),
            "ms": int((now - started) * 1000),
            "chars": len(str(result.get("text") or "")),
            "tools": len(calls),
            "tools_ok": sum(1 for c in calls if c.get("_ok") is not False),
            "tools_failed": sum(1 for c in calls if c.get("_ok") is False),
        })
        try:
            limit = max(1, min(1000, int(config.get("assistant_metrics_limit", 200))))
        except (TypeError, ValueError):
            limit = 200
        self.metrics = self.metrics[-limit:]

    async def _call_model(self, messages: list[dict], token: str = "", emit=None,
                          device: str = "", question: str = "") -> dict:
        backend = self._route(messages, question, device)
        errors = []
        attempted = []
        started = time.time()
        stream = emit is not None
        if backend in ("gemini", "hybrid"):
            if not self._key() and backend == "gemini":
                errors.append("gemini: no API key configured")
            elif self._key():
                attempted.append("gemini")
                try:
                    result = await asyncio.to_thread(self._gemini, messages, token, emit,
                                                     device, stream, question)
                    self._record_metric("gemini", started, result, device)
                    return result
                except self._model_errors() as e:
                    errors.append("gemini: %s" % e)
        # A Gemini-only image route still has a local multimodal model available; use it rather
        # than failing the turn when the cloud key or endpoint is unusable.
        if (backend == "gemini" and errors and _vision_model()
                and any(m.get("images") for m in messages)):
            if stream and attempted:
                await self._emit(emit, "stream_reset", token=token, device=device)
            attempted.append("local")
            await self._emit(emit, "fallback", text="local", token=token, device=device)
            try:
                result = await asyncio.to_thread(self._local, messages, token, emit,
                                                 device, stream, question)
                self._record_metric("local", started, result, device, fallback=True)
                return result
            except self._model_errors() as e:
                errors.append("local: %s" % e)
        if backend in ("local", "hybrid"):
            if stream and attempted:
                await self._emit(emit, "stream_reset", token=token, device=device)
            attempted.append("local")
            if len(attempted) > 1:
                await self._emit(emit, "fallback", text="local", token=token, device=device)
            try:
                result = await asyncio.to_thread(self._local, messages, token, emit,
                                                 device, stream, question)
                self._record_metric("local", started, result, device,
                                    fallback=len(attempted) > 1)
                return result
            except self._model_errors() as e:
                errors.append("local: %s" % e)
        raise RuntimeError("; ".join(errors) or "no assistant backend configured")

    def _validate_tool_call(self, call: dict) -> tuple[dict | None, str | None]:
        """Validate against built-in and plugin-supplied tool specifications."""
        specs = dict(TOOL_BY_NAME)
        specs.update(self.tool_specs)
        return _validate_call(call, self.tools, specs)

    @staticmethod
    def _tool_message(name: str, run_result: dict) -> dict:
        payload = {k: v for k, v in run_result.items() if k != "images"}
        return _tool_result_message(name, payload, images=run_result.get("images"))

    def _remember_undo(self, call: dict, result: dict, device: str = "") -> None:
        """Keep a best-effort inverse for tools that changed local state."""
        if not isinstance(result, dict) or not result.get("ok"):
            return
        name, args = str(call.get("name") or ""), call.get("args") or {}
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        entry = None
        if name == "remember" and args.get("text"):
            entry = {"kind": "forget", "text": str(args["text"])[:1000]}
        elif name == "forget" and args.get("text"):
            entry = {"kind": "remember", "text": str(args["text"])[:1000], "category": "fact"}
        elif name == "save_draft" and payload.get("path"):
            entry = {"kind": "delete_draft", "path": str(payload["path"])}
        elif name == "task_add" and payload.get("task"):
            entry = {"kind": "remove_task_line", "task": str(payload["task"])}
        elif name == "calendar_add" and payload.get("uid"):
            entry = {"kind": "delete_calendar", "uid": str(payload["uid"])}
        if entry is None:
            return
        key = str(device or self._current_device or "")
        stack = self.undo_log.setdefault(key, [])
        stack.append({"tool": name, **entry})
        self.undo_log[key] = stack[-20:]

    def undo_last(self, device: str = "") -> dict:
        """Reverse the newest reversible action for this device, or report that there is none."""
        key = str(device or self._current_device or "")
        stack = self.undo_log.get(key) or []
        if not stack:
            return {"ok": False, "error": "nothing to undo"}
        entry = stack.pop()
        self.undo_log[key] = stack
        kind = str(entry.get("kind") or "")
        try:
            if kind == "forget" and self.store is not None:
                result = self.store.forget(str(entry.get("text") or ""))
            elif kind == "remember" and self.store is not None:
                result = self.store.remember(str(entry.get("text") or ""),
                                             str(entry.get("category") or "fact"))
            elif kind == "delete_draft":
                result = delete_draft(str(entry.get("path") or ""))
            elif kind == "remove_task_line":
                result = remove_task_line(str(entry.get("task") or ""))
            elif kind == "delete_calendar":
                result = delete_calendar_event(str(entry.get("uid") or ""))
            else:
                result = {"ok": False, "error": "that action cannot be undone"}
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)}
        return {"ok": bool(result.get("ok")), "reversed": entry.get("tool"), **result}

    async def _run_tool(self, call: dict, device: str = "") -> dict:
        name, args = call.get("name"), call.get("args") or {}
        fn = self.tools.get(name)
        if fn is None:
            call["_ok"] = False
            return {"ok": False, "error": "unknown tool %s" % name}
        changing = name in CONFIRM_TOOLS or name in {
            "remember", "forget", "save_draft", "timer", "task_add", "calendar_add", "undo",
        }
        if self.dry_run and changing:
            call["_ok"] = True
            return {"ok": True, "dry_run": True, "result": {"tool": name, "args": args}}
        try:
            result = fn(**args) if isinstance(args, dict) else fn(args)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) and result.get("image"):
                images = [str(result.pop("image"))]
                out = {"ok": True, "result": result, "images": images}
            else:
                out = {"ok": True, "result": result}
            call["_ok"] = bool(out.get("ok"))
            self.tool_metrics.append({"tool": name, "ok": call["_ok"], "ts": int(time.time())})
            self.tool_metrics = self.tool_metrics[-200:]
            self._remember_undo(call, out, device)
            return out
        except Exception as e:  # noqa: BLE001
            call["_ok"] = False
            self.tool_metrics.append({"tool": name, "ok": False, "ts": int(time.time())})
            return {"ok": False, "error": str(e)}

    async def _emit(self, emit, op: str, **data):
        event = {"op": op, **data}
        self._plugin_emit(op, event)
        target = emit or self.broadcast
        if target is None:
            return
        result = target(event)
        if inspect.isawaitable(result):
            await result

    async def _verify_answer(self, messages: list[dict], draft: str, token: str,
                             device: str, question: str) -> str:
        """One quiet review pass for complex answers. Failure simply keeps the original draft."""
        if not draft:
            return draft
        review = [dict(m) for m in messages]
        review.extend([
            {"role": "assistant", "content": draft},
            {"role": "user", "content": "Review that draft for factual or tool-result errors. "
                                        "Reply with only the corrected final answer. Do not use tools."},
        ])
        try:
            result = await self._call_model(review, token, None, device, question)
            if result.get("calls") or not str(result.get("text") or "").strip():
                return draft
            await self._emit(None, "verified", token=token, device=device)
            return str(result["text"]).strip()[:MAX_ANSWER_CHARS]
        except Exception:  # noqa: BLE001
            return draft

    async def _handle_calls(self, messages: list[dict], calls: list[dict], token: str,
                            emit, rounds: int, question: str = "", device: str = "") -> dict | None:
        """Run tool calls; pause on the first one that needs confirmation.

        An exact tool+argument signature in ``assistant_auto_allow`` skips the prompt, which is how
        the Spot's "always" approval is persisted without opening up whole tool categories.
        """
        for i, call in enumerate(calls):
            name = call.get("name")
            args = call.get("args") or {}
            self.turn_trace.append(str(name))
            await self._emit(emit, "tool", text=name, token=token, device=device)
            if name in CONFIRM_TOOLS:
                signature = _call_signature(call)
                allowed = config.get("assistant_auto_allow", {}) or {}
                if isinstance(allowed, dict) and allowed.get(signature):
                    result = await self._run_tool(call, device)
                    messages.append(self._tool_message(name, result))
                    continue
                pretty = ", ".join("%s=%s" % (k, str(v)[:60]) for k, v in args.items())
                prompt = "Run %s (%s)?" % (name, pretty[:180])
                self.pending[token] = {
                    "messages": list(messages),
                    "remaining_calls": list(calls[i:]),
                    "rounds": rounds,
                    "question": question,
                    "created": time.time(),
                    "signature": signature,
                    "tool": name,
                    "device": device,
                    "dry_run": self.dry_run,
                }
                out = {"op": "confirm", "token": token, "tool": name, "text": prompt,
                       "device": device, "expires_s": _confirm_timeout(),
                       "dry_run": self.dry_run}
                audio = await asyncio.to_thread(_confirm_audio, prompt)
                if audio:
                    out["audio"] = audio
                return out
            result = await self._run_tool(call, device)
            messages.append(self._tool_message(name, result))
        return None

    async def _resume(self, messages: list[dict], calls: list[dict], token: str,
                      emit, rounds: int, question: str = "", device: str = "") -> dict:
        outcome = await self._handle_calls(messages, calls, token, emit, rounds, question, device)
        if outcome is not None:
            return outcome
        return await self._loop(messages, token, emit, rounds - 1, question, device)

    async def _loop(self, messages: list[dict], token: str, emit=None,
                    rounds: int | None = None, question: str = "", device: str = "",
                    retries: int = 0) -> dict:
        remaining = MAX_TOOL_ROUNDS if rounds is None else max(0, int(rounds))

        def reply(text: str) -> dict:
            return {"op": "reply", "text": text, "token": token, "device": device,
                    "trace": list(dict.fromkeys(self.turn_trace))}

        def finish(text: str) -> dict:
            """Save and return an authoritative answer that bypassed the model draft."""
            value = str(text or "").strip()[:MAX_ANSWER_CHARS]
            asked = question or next((m["content"] for m in reversed(messages)
                                      if m.get("role") == "user"), "")
            self.history.extend([{"role": "user", "content": asked},
                                 {"role": "assistant", "content": value}])
            self.history = self.history[-8:]
            if self.store is not None:
                try:
                    self.store.append_turn(asked, value)
                except Exception:  # noqa: BLE001
                    pass
            return reply(value)

        for index in range(remaining):
            if self.is_cancelled(token):
                await self._emit(emit, "cancelled", token=token, device=device)
                return reply("Cancelled.")
            answer = await self._call_model(messages, token, emit, device, question)
            if self.is_cancelled(token):
                await self._emit(emit, "cancelled", token=token, device=device)
                return reply("Cancelled.")

            calls = list(answer.get("calls") or [])
            if not calls:
                recovered, remainder = calls_from_text(str(answer.get("text") or ""),
                                                       set(self.tools))
                if recovered:
                    calls = recovered
                    answer = {**answer, "text": remainder}
            valid, rejected = [], []
            for raw in calls:
                if raw.get("name") not in self.tools:
                    continue
                clean, error = self._validate_tool_call(raw)
                if error:
                    rejected.append(error)
                    await self._emit(emit, "tool_error", text=error, token=token, device=device)
                    messages.append(self._tool_message(str(raw.get("name") or "tool"),
                                                       {"ok": False, "error": error}))
                else:
                    valid.append(clean)
            if rejected:
                if retries < 1 and remaining - index > 1:
                    messages.append({"role": "user", "content":
                                     "Those tool calls were rejected: %s. Retry once with valid "
                                     "arguments, or answer without the tool." % "; ".join(rejected[:3])})
                    return await self._loop(messages, token, emit, remaining - index - 1,
                                            question, device, retries + 1)
                return reply("I couldn't use that tool safely. " + rejected[0][:160])

            needed = required_tool(routing_hint(question, set(self.tools)))
            if (not valid and needed in self.tools and retries < 1 and remaining - index > 1
                    and not any(m.get("role") == "tool" and m.get("name") == needed
                                for m in messages[-6:])):
                messages.append({"role": "system", "content":
                                 _must_call(needed) + " Your previous reply did not call it; "
                                 "call it now and do not answer yet."})
                return await self._loop(messages, token, emit, remaining - index - 1,
                                        question, device, retries + 1)

            if (not valid and retries >= 1
                    and (needed in READ_ONLY_AUTO_TOOLS or needed in DIRECT_FACT_TOOLS)
                    and not any(m.get("role") == "tool" and m.get("name") == needed
                                for m in messages[-6:])):
                args = synthetic_args(needed, question)
                if args is not None:
                    clean, error = self._validate_tool_call({"name": needed, "args": args})
                    if not error:
                        valid.append(clean)

            if not valid:
                text, _pauses = pause_markers(
                    (answer["text"] or "I couldn't come up with an answer.")[:MAX_ANSWER_CHARS])
                if (config.get("assistant_verify_complex", True)
                        and _is_complex(question, messages) and token not in self._verified):
                    self._verified.add(token)
                    text = await self._verify_answer(messages, text, token, device, question) or text
                asked = question or next((m["content"] for m in reversed(messages)
                                          if m.get("role") == "user"), "")
                self.history.extend([{"role": "user", "content": asked},
                                     {"role": "assistant", "content": text}])
                self.history = self.history[-8:]
                if self.store is not None:
                    try:
                        self.store.append_turn(asked, text)
                        threshold = int(config.get("assistant_memory_compact_after", 40) or 40)
                        if threshold > 0 and hasattr(self.store, "compact_turns"):
                            self.store.compact_turns(keep=threshold)
                    except Exception:  # noqa: BLE001
                        pass
                return reply(text)

            messages.append(_tool_calls_message(valid))
            outcome = await self._handle_calls(messages, valid, token, emit,
                                               remaining - index, question, device)
            if outcome is not None:
                return {**outcome, "trace": list(dict.fromkeys(self.turn_trace))}

            # Sabres answers must carry every detail the local tool looked up (time, teams,
            # venue, TV/status, jersey). The model often drops TV, so use the authoritative
            # one-line summary directly when this was the only tool in the turn.
            if needed == "sabres_schedule" and self.turn_trace == ["sabres_schedule"]:
                tool_msg = next((m for m in reversed(messages)
                                 if m.get("role") == "tool" and m.get("name") == "sabres_schedule"),
                                None)
                if tool_msg:
                    try:
                        payload = json.loads(str(tool_msg.get("content") or ""))
                    except json.JSONDecodeError:
                        payload = {}
                    detail = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                    summary = str(detail.get("summary") or "").strip() if isinstance(detail, dict) else ""
                    if summary:
                        return finish(summary)

            # VRAM is a fixed hardware fact from ADL/NVML. The model sometimes ignores the field
            # and says it has no information, so answer from the tool result for a VRAM-only ask.
            if (needed == "pc_stats" and self.turn_trace == ["pc_stats"]
                    and re.search(r"\bvram\b|graphics card|video memory",
                                  str(question or ""), re.I)):
                tool_msg = next((m for m in reversed(messages)
                                 if m.get("role") == "tool" and m.get("name") == "pc_stats"), None)
                if tool_msg:
                    try:
                        payload = json.loads(str(tool_msg.get("content") or ""))
                    except json.JSONDecodeError:
                        payload = {}
                    detail = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                    vram = detail.get("vram_gb") if isinstance(detail, dict) else None
                    if vram not in (None, ""):
                        try:
                            amount = f"{float(vram):g}"
                        except (TypeError, ValueError):
                            amount = str(vram)
                        return finish(f"Your graphics card has {amount} GB of VRAM.")
            token = _new_token()
        return reply("I ran out of tool steps. Try a shorter request.")

    async def ask(self, text: str, emit=None, device: str = "", dry_run: bool = False) -> dict:
        """Return a reply dict, or a confirm dict that must be resolved with confirm()."""
        self._async_loop = asyncio.get_running_loop()
        self.cancel_all = False
        self.dry_run = bool(dry_run)
        self._current_device = str(device or "")
        self.turn_trace = []
        token = _new_token()
        question = str(text)[:2000]
        direct = clock_answer(question)
        if direct:
            await self._emit(emit, "prompt", text=question[:200], token=token, device=device)
            return {"op": "reply", "text": direct, "token": token, "device": device,
                    "trace": []}
        # Earlier assistant text is unverified background: the local model can otherwise copy a
        # stale "I can't check that" reply instead of calling the tool that is available now.
        recent = []
        for item in self.history[-6:]:
            entry = dict(item)
            if entry.get("role") == "assistant" and entry.get("content"):
                entry["content"] = ("Earlier answer (unverified, do not repeat): "
                                    + str(entry["content"]))
            recent.append(entry)
        messages = [{"role": "system", "content": system_prompt(device)}, *recent]
        hint = routing_hint(question, set(self.tools))
        if hint:
            messages.append({"role": "system", "content": hint})
        messages.append({"role": "user", "content": question})
        await self._emit(emit, "prompt", text=question[:200], token=token, device=device)
        await self._emit(emit, "thinking", text="", token=token, device=device)
        return await self._loop(messages, token, emit, question=question, device=device)

    async def confirm(self, token: str, yes: bool, emit=None, always: bool = False,
                      device: str = "") -> dict:
        self._async_loop = asyncio.get_running_loop()
        key = str(token)
        state = self.pending.pop(key, None)
        if state is None or time.time() - float(state.get("created", 0)) > _confirm_timeout():
            return {"op": "error", "text": "that confirmation expired", "token": key, "device": device}
        calls = list(state.get("remaining_calls") or [])
        self.dry_run = bool(state.get("dry_run", self.dry_run))
        self._current_device = str(state.get("device") or device or "")
        self.turn_trace = list(dict.fromkeys(self.turn_trace))
        if not calls:
            return {"op": "error", "text": "that confirmation expired", "token": key, "device": device,
                    "trace": self.turn_trace}
        call = calls.pop(0)
        name = call.get("name")
        if yes:
            if always:
                signature = str(state.get("signature") or "")
                if signature:
                    allowed = dict(config.get("assistant_auto_allow", {}) or {})
                    allowed[signature] = True
                    config.set_value("assistant_auto_allow", allowed)
            result = await self._run_tool(call, self._current_device)
        else:
            result = {"ok": False, "declined": True, "tool": name}
        messages = list(state["messages"])
        messages.append(self._tool_message(name, result))
        result = await self._resume(messages, calls, _new_token(), emit,
                                    int(state.get("rounds", MAX_TOOL_ROUNDS)),
                                    str(state.get("question") or ""),
                                    self._current_device)
        return {**result, "trace": list(dict.fromkeys(self.turn_trace))}

    def status(self) -> dict:
        selected_backend, selected_model = _parse_model_choice(current_model_choice())
        backend = selected_backend or self._backend()
        usable = backend in ("local", "hybrid") or bool(self._key())
        return {"backend": backend, "model": selected_model,
                "model_choice": current_model_choice(),
                "ready": bool(usable), "tools": sorted(self.tools),
                "pending": len(self.pending), "metrics": len(self.metrics),
                "fallbacks": sum(1 for m in self.metrics if m.get("fallback")),
                "tool_success": sum(1 for t in self.tool_metrics if t.get("ok")),
                "tool_failures": sum(1 for t in self.tool_metrics if not t.get("ok")),
                "memory": self.store.stats() if self.store is not None else None}


def decode_inline(data: str) -> bytes:
    """Utility for tests/tools that receive base64 text from a model."""
    return base64.b64decode(data)
