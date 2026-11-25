"""Speak assistant replies on the EchoSpot.

Two offline backends, selected by ``tts_backend`` in config.json:

  "auto"  (default) Piper neural voice when the model is installed, Windows SAPI otherwise
  "piper"           prefer Piper, fall back to SAPI if it fails
  "sapi"            Windows SAPI only

Both write a WAV under media/tts/ and hand the Spot a media URL, so nothing leaves the LAN. If
synthesis fails the caller simply omits the audio field and the face shows text.

Piper ships as a small standalone executable beside the agent (no Python packages, no API key,
no internet at reply time). Its British male model sounds considerably more natural than the
en-US voices built into Windows. SAPI's ``tts_style`` preset controls rate/pitch and voice choice
as a fallback; SAPI ignores SSML <prosody pitch>, so any pitch change is applied to the WAV itself.
"""
import os
import re
import subprocess
import time
import uuid
import wave

import config

_FOLDER = None
_WARNED = set()
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.onnx$")
DEFAULT_MODEL = "en_GB-alan-medium.onnx"

# Language is the hex LCID: 409 = en-US, 809 = en-GB. Gender comes from the voice token.
STYLES = {
    "jarvis": {
        # Keep the natural pace and pitch: SAPI rate steps are coarse and resampling the WAV
        # adds artifacts. The style comes from the neural voice and the wording prompt; these
        # remain available as explicit config overrides for anyone who wants them.
        "rate": 0,           # SAPI rate steps, -10..10
        "pitch": 0,          # semitones; negative is deeper
        "volume": 100,       # percent
        "language": "809",
        "gender": "Male",
        "prefer": ("george", "ryan", "daniel", "arthur", "oliver"),
    },
    "natural": {
        "rate": 0,
        "pitch": 0,
        "volume": 100,
        "language": "",
        "gender": "",
        "prefer": (),
    },
}


def folder() -> str:
    """Writable directory next to config.json, also served by the agent's /media/ route."""
    global _FOLDER
    if _FOLDER is None:
        _FOLDER = config.data_path("media", "tts")
    os.makedirs(_FOLDER, exist_ok=True)
    return _FOLDER


def _warn(key: str, message: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        print("tts: " + message)


def _number(name: str, fallback, low: int, high: int):
    value = config.get(name, None)
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _device_prefs(device: str = "") -> dict:
    devices = config.get("assistant_devices", {}) or {}
    prefs = devices.get(str(device or ""), {}) if isinstance(devices, dict) else {}
    return dict(prefs) if isinstance(prefs, dict) else {}


def _override(value, prefs: dict, key: str, low: int, high: int):
    try:
        return max(low, min(high, int(prefs.get(key, value))))
    except (TypeError, ValueError):
        return value


def style_settings(device: str = "") -> dict:
    """Resolved SAPI settings: preset defaults, config overrides, then per-device overrides."""
    prefs = _device_prefs(device)
    name = str(prefs.get("tts_style") or config.get("tts_style", "jarvis") or "jarvis").strip().lower()
    if name not in STYLES:
        name = "natural"
    preset = STYLES[name]
    return {
        "style": name,
        "rate": _override(_number("tts_rate", preset["rate"], -10, 10), prefs, "tts_rate", -10, 10),
        "pitch": _override(_number("tts_pitch", preset["pitch"], -8, 8), prefs, "tts_pitch", -8, 8),
        "volume": _override(_number("tts_volume", preset["volume"], 0, 100), prefs, "tts_volume", 0, 100),
        "voice": str(prefs.get("tts_voice") or config.get("tts_voice", "") or "").strip(),
        "language": preset["language"],
        "gender": preset["gender"],
        "prefer": preset["prefer"],
        "length_scale": _length_scale(prefs),
    }


def _length_scale(prefs: dict | None = None) -> float:
    value = (prefs or {}).get("tts_length_scale",
                              config.get("tts_piper_length_scale", 1.0))
    try:
        return max(0.5, min(2.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _piper_folder() -> str | None:
    """The installed runtime folder, or the source packaging folder during development."""
    # A user can drop replacement voices beside the exe; PyInstaller puts the bundled copy in
    # _internal/, which is where bundled_path() resolves when frozen.
    candidates = [config.data_path("piper"), config.bundled_path("piper")]
    if not getattr(config, "FROZEN", False):
        candidates.append(config.bundled_path("..", "packaging", "piper"))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "piper.exe")):
            return candidate
    return None


def piper_info() -> dict:
    """Report whether the Piper backend is usable, and with which model."""
    model = str(config.get("tts_piper_model", DEFAULT_MODEL) or DEFAULT_MODEL)
    if not MODEL_RE.match(model):
        model = DEFAULT_MODEL
    runtime = _piper_folder()
    available = bool(runtime and os.path.isfile(os.path.join(runtime, model)))
    return {"available": available, "model": model, "folder": runtime}


def voices() -> list[dict]:
    """Installed SAPI voices, for diagnostics and the README's selection advice."""
    result = []
    engine = None
    try:
        import pythoncom  # noqa: PLC0415 - optional at runtime
        import win32com.client  # noqa: PLC0415 - optional at runtime
        pythoncom.CoInitialize()
        try:
            engine = win32com.client.Dispatch("SAPI.SpVoice")
            for token in engine.GetVoices():
                result.append({
                    "name": str(token.GetDescription()),
                    "language": str(token.GetAttribute("Language") or ""),
                    "gender": str(token.GetAttribute("Gender") or ""),
                })
        finally:
            token = None  # the loop variable still holds the last COM proxy
            engine = None  # release COM proxies before leaving the apartment
            pythoncom.CoUninitialize()
    except Exception as e:  # noqa: BLE001
        _warn("voices", "could not list voices (%s)" % e)
    return result


def _pick_voice(engine, settings: dict):
    """Return a SAPI token for the configured voice, or None to keep the default."""
    requested = str(settings.get("voice") or "").strip()
    try:
        tokens = list(engine.GetVoices())
    except Exception:  # noqa: BLE001
        return None
    if requested:
        for token in tokens:
            if requested.lower() in str(token.GetDescription()).lower():
                return token
        _warn("requested", "voice %r is not installed; using the best available voice" % requested)
    if not settings["language"] and not settings["gender"]:
        return None
    scored = []
    for token in tokens:
        score = 0
        description = str(token.GetDescription()).lower()
        if settings["language"] and str(token.GetAttribute("Language") or "").lower().startswith(
                settings["language"].lower()):
            score += 2
        if settings["gender"] and str(token.GetAttribute("Gender") or "").lower() == settings["gender"].lower():
            score += 1
        if any(name in description for name in settings["prefer"]):
            score += 3
        if score:
            scored.append((score, token))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = scored[0][1]
    _warn("selected", "using voice %s" % chosen.GetDescription())
    return chosen


def _piper_speak(text: str, path: str, settings: dict | None = None) -> bool:
    """Render with the standalone Piper executable. True when a WAV was produced."""
    info = piper_info()
    if not info["available"]:
        return False
    length_scale = (settings or {}).get("length_scale", _length_scale())
    command = [
        os.path.join(info["folder"], "piper.exe"),
        "--model", os.path.join(info["folder"], info["model"]),
        "--output_file", path,
        "--length_scale", str(length_scale),
        "--quiet",
    ]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(command, input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=30, creationflags=flags)
        if result.returncode != 0:
            _warn("piper", "exit %d: %s" % (result.returncode, result.stderr.decode("utf-8", "replace")[:160]))
            return False
        return os.path.isfile(path) and os.path.getsize(path) > 100
    except Exception as e:  # noqa: BLE001
        _warn("piper", "could not render (%s)" % e)
        return False


def _shift_pitch(path: str, semitones: int) -> None:
    """Resample a WAV so speech is deeper (and a little slower) by ``semitones``."""
    if not semitones:
        return
    try:
        import numpy as np  # noqa: PLC0415 - already a project dependency

        factor = 2 ** (semitones / 12)
        if not 0.5 <= factor <= 2:
            return
        with wave.open(path, "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        if width != 2 or channels < 1:
            return
        samples = np.frombuffer(raw, dtype=np.int16)
        per = samples.size // channels
        if per < 512:
            return
        count = int(per / factor)
        destination = np.arange(count) * factor
        source = np.arange(per)
        shifted = np.empty(count * channels, dtype=np.int16)
        for channel in range(channels):
            part = samples[channel * per:(channel + 1) * per]
            shifted[channel * count:(channel + 1) * count] = np.interp(
                destination, source, part).astype(np.int16)
        temporary = path + ".tmp"
        with wave.open(temporary, "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(rate)
            out.writeframes(shifted.tobytes())
        os.replace(temporary, path)
    except Exception as e:  # noqa: BLE001
        _warn("pitch", "skipping pitch shift (%s)" % e)


def _sapi_speak(text: str, path: str, settings: dict) -> bool:
    """Render with Windows SAPI. True when a WAV was produced."""
    uninit = None
    engine = stream = token = None
    try:
        import pythoncom  # noqa: PLC0415 - pywin32 ships with the agent
        import win32com.client  # noqa: PLC0415 - optional at runtime
        pythoncom.CoInitialize()  # to_thread() workers are MTA; SAPI needs COM initialized
        uninit = pythoncom.CoUninitialize
        engine = win32com.client.Dispatch("SAPI.SpVoice")
        token = _pick_voice(engine, settings)
        if token is not None:
            engine.Voice = token
        engine.Rate = settings["rate"]
        engine.Volume = settings["volume"]
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(path, 3)  # SSFMCreateForWrite (SpeechStreamFileMode)
        try:
            engine.AudioOutputStream = stream
            engine.Speak(text)
        finally:
            engine.AudioOutputStream = None
            stream.Close()
        _shift_pitch(path, settings["pitch"])
        return os.path.isfile(path) and os.path.getsize(path) > 100
    except Exception as e:  # noqa: BLE001
        print("tts: SAPI unavailable (%s)" % e)
        return False
    finally:
        stream = token = engine = None  # release COM proxies before leaving the apartment
        if uninit is not None:
            uninit()


def speak(text: str, device: str = "") -> str | None:
    """Synthesize a short reply. Returns a media-relative path like 'tts/abc.wav', or None."""
    marker = str(config.get("assistant_pause_marker", "[pause]") or "").strip()
    if marker:
        text = str(text).replace(marker, " ")
    text = " ".join(str(text).split())[:500]
    if not text:
        return None
    name = "%s.wav" % uuid.uuid4().hex[:12]
    path = os.path.join(folder(), name)
    settings = style_settings(device)
    backend = str(config.get("tts_backend", "auto") or "auto").strip().lower()
    if backend in ("auto", "piper"):
        piper_args = (text, path, settings) if _device_prefs(device) else (text, path)
        if _piper_speak(*piper_args):
            return "tts/" + name
        if backend == "piper":
            _warn("fallback", "Piper unavailable; falling back to SAPI")
    if _sapi_speak(text, path, settings):
        return "tts/" + name
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    return None


def cleanup(max_age_s: int = 3600, keep: int = 40) -> None:
    """Keep the folder small; the Spot may replay a reply for a minute at most."""
    path = folder()
    files = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isfile(full) and name.lower().endswith(".wav"):
            files.append((os.path.getmtime(full), full))
    files.sort(reverse=True)
    now = time.time()
    for i, (mtime, full) in enumerate(files):
        if i >= keep or now - mtime > max_age_s:
            try:
                os.remove(full)
            except OSError:
                pass
