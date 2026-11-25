"""Voice channel for the EchoPortal agent: receives PCM from the Spot, transcribes it, types on the PC.

Two backends, chosen with `voice_backend` in config.json:

  "whisper"  local faster-whisper (needs requirements-voice.txt and a CUDA GPU to be fast)
  "gemini"   Google's generateContent REST API (no local model, works on any GPU, needs a key)
  "auto"     Gemini when a key is configured, otherwise Whisper (default)

Protocol (channel "voice"):
  Spot -> PC   {"op":"start","rate":16000}   then binary frames of 16-bit mono PCM   then {"op":"stop"} | {"op":"cancel"}
  PC -> Spot   {"op":"transcript","text":..., "target": "<focused window title>"}
  Spot -> PC   {"op":"send","text":..., "enter":true|false, "target":"focused"|"claude"}
  PC -> Spot   {"op":"sent","window":...} | {"op":"error","text":...}
"""
import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import wave

import numpy as np

import config
import typer

MODEL_NAME = "large-v3-turbo"
GEMINI_DEFAULT_MODEL = "gemini-flash-lite-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
GEMINI_PROMPT = "Transcribe this audio exactly. Output only the spoken words, no commentary."

# The Spot's mic peaks around -34 dBFS (0.02). Anything below this is room noise or silence.
# Amplifying that to full scale is what made Gemini answer 7 s of silence with a whole sentence.
SILENCE_PEAK = 0.006
SILENCE_RMS = 0.002
GAIN_MAX = 60.0
MAX_UTTERANCE_SECS = 32.0

_model = None
_model_lock = threading.Lock()
_device_used = "?"
# Set by agent.py: an async callable(text) -> dict | None. It turns "set a timer for 10 minutes"
# into a timer and returns a confirmation; None means the text is ordinary dictation.
COMMAND_HANDLER = None
# Set by agent.py when the "assistant" face is enabled: an async callable(text) -> dict | None.
# The Spot flags a recording with {"assistant": true} when the Jarvis target is selected.
ASSISTANT_HANDLER = None


# --------------------------------------------------------------------------- settings
def gemini_key() -> str:
    """Environment wins over config.json, so a key can stay out of a synced folder."""
    return (os.environ.get("GEMINI_API_KEY") or config.as_str("gemini_api_key") or "").strip()


def gemini_model() -> str:
    return (config.as_str("gemini_model", GEMINI_DEFAULT_MODEL) or GEMINI_DEFAULT_MODEL).strip()


def backend() -> str:
    """Resolve voice_backend. 'auto' prefers Gemini only when a key exists."""
    choice = (config.as_str("voice_backend", "auto") or "auto").strip().lower()
    if choice in ("gemini", "whisper"):
        return choice
    return "gemini" if gemini_key() else "whisper"


def status() -> str:
    """One line for the startup log."""
    b = backend()
    if b == "gemini":
        return "voice: Gemini transcription (%s)%s" % (
            gemini_model(), "" if gemini_key() else " - NO API KEY, set gemini_api_key or GEMINI_API_KEY")
    return "voice: local Whisper (%s)" % MODEL_NAME


# --------------------------------------------------------------------------- whisper
def load_model():
    """Load Whisper once (GPU fp16, falling back to CPU int8). Safe to call from a thread at startup."""
    global _model, _device_used
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel
        t0 = time.time()
        try:
            _model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
            _device_used = "cuda"
        except Exception as e:  # noqa: BLE001
            print("whisper: CUDA load failed (%s), using CPU int8" % e)
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
            _device_used = "cpu"
        print("whisper: %s loaded on %s in %.1fs" % (MODEL_NAME, _device_used, time.time() - t0))
        return _model


# --------------------------------------------------------------------------- audio
def prepare(pcm: bytes, rate: int) -> np.ndarray:
    """PCM16 -> float32 mono at 16 kHz, with the quiet-mic gain the Spot needs.

    Returns an empty array for silence/noise, so callers can skip the API call entirely.
    """
    # 16-bit mono means an even byte count; a stray odd byte would make frombuffer raise.
    pcm = pcm[:len(pcm) - (len(pcm) % 2)]
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if not len(audio):
        return audio
    peak_in = float(np.abs(audio).max())
    rms_in = float(np.sqrt(np.mean(audio * audio)))
    if peak_in < SILENCE_PEAK and rms_in < SILENCE_RMS:
        return np.zeros(0, dtype=np.float32)
    if rate != 16000:
        # crude resample; the Spot sends 16 kHz so this is only a safety net
        idx = np.arange(0, len(audio), rate / 16000.0)
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    # The Spot's mic array is very quiet (~-34 dBFS peaks); normalise so the model sees normal levels.
    # The cap stops a noisy floor from being amplified into what sounds like speech.
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if 0 < peak < 0.3:
        audio = audio * min(0.5 / peak, GAIN_MAX)
    return audio


def wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    """float32 [-1, 1] -> a mono 16-bit WAV file in memory."""
    pcm16 = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- gemini
def _gemini_error(payload: dict) -> str:
    """Pull the useful sentence out of a Gemini error/block response."""
    err = payload.get("error") or {}
    if err.get("message"):
        return str(err["message"])
    feedback = payload.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return "audio blocked by Gemini (%s)" % feedback["blockReason"]
    candidates = payload.get("candidates") or []
    if candidates:
        reason = candidates[0].get("finishReason")
        if reason and reason != "STOP":
            return "Gemini returned no transcript (%s)" % reason
    return "Gemini returned no transcript"


def parse_gemini(payload: dict) -> str:
    """Extract the transcript text from a generateContent response."""
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(_gemini_error(payload)) from None
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError(_gemini_error(payload))
    return text


def gemini_transcribe(audio: np.ndarray, rate: int = 16000, timeout: float = 45.0) -> str:
    """Send the clip to Gemini and return the transcript. Blocking; call from a worker thread."""
    key = gemini_key()
    if not key:
        raise RuntimeError("no Gemini API key: set gemini_api_key in config.json or GEMINI_API_KEY")

    body = {
        "contents": [{
            "parts": [
                {"text": GEMINI_PROMPT},
                {"inline_data": {
                    "mime_type": "audio/wav",
                    "data": base64.b64encode(wav_bytes(audio, rate)).decode("ascii"),
                }},
            ],
        }],
        "generationConfig": {"temperature": 0},
    }
    req = urllib.request.Request(
        GEMINI_URL % gemini_model(),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    payload = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = _gemini_error(json.load(e))
            except Exception:  # noqa: BLE001
                detail = e.reason or ""
            # 503 (high demand) and 429 (rate limit) are usually momentary; one retry is cheap.
            if e.code in (429, 503) and attempt == 0:
                time.sleep(0.8)
                continue
            raise RuntimeError("Gemini HTTP %s: %s" % (e.code, detail)) from None
        except urllib.error.URLError as e:
            raise RuntimeError("cannot reach Gemini: %s" % (e.reason,)) from None
        except TimeoutError:
            raise RuntimeError("Gemini request timed out") from None
    return parse_gemini(payload)


# --------------------------------------------------------------------------- dispatch
def transcribe_pcm(pcm: bytes, rate: int) -> str:
    audio = prepare(pcm, rate)
    if not audio.size:
        return ""
    if backend() == "gemini":
        return gemini_transcribe(audio, 16000)

    try:
        m = load_model()
    except ImportError:
        raise RuntimeError(
            "no speech backend: set gemini_api_key in config.json (or GEMINI_API_KEY), "
            "or install pc-agent/requirements-voice.txt for local Whisper"
        ) from None
    segs, _ = m.transcribe(audio, beam_size=5, language="en", vad_filter=True,
                           condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


class VoiceSession:
    """Per-WebSocket-client state."""

    def __init__(self, ws, device=""):
        self.ws = ws
        self.device = str(device or "")[:64]
        self.buf = bytearray()
        self.rate = 16000
        self.active = False
        self.started = 0.0
        self.frames = 0
        self.overflowed = False

    async def reply(self, **data):
        await self.ws.send(json.dumps({"ch": "voice", "data": data}))

    def on_binary(self, frame: bytes):
        if not self.active:
            return
        # The Spot stops at 30 s, but nothing stops a buggy or hostile client from streaming
        # forever into RAM. Keep the first 32 s and discard the rest.
        limit = int(self.rate * 2 * MAX_UTTERANCE_SECS)
        room = limit - len(self.buf)
        if len(frame) > room:
            if not self.overflowed:
                self.overflowed = True
                print("voice: hit %.0fs cap, discarding the rest" % MAX_UTTERANCE_SECS)
            frame = frame[:max(room, 0)]
        # Drop a stray trailing byte so the buffer stays valid PCM16 (and the WAV stays aligned).
        if len(frame) % 2:
            frame = frame[:-1]
        if not frame:
            return
        self.buf += frame
        self.frames += 1
        if self.frames % 50 == 0:   # every second
            a = np.frombuffer(bytes(frame), dtype=np.int16).astype(np.float32) / 32768.0
            print("voice: %d frames, %.1fs, last-frame rms %.4f" % (self.frames, len(self.buf) / 2 / self.rate, float(np.sqrt(np.mean(a * a))) if len(a) else 0))

    async def on_message(self, data: dict, loop):
        op = data.get("op")
        print("voice: op=%s" % op)
        if op == "start":
            self.frames = 0
            self.buf = bytearray()
            self.overflowed = False
            # Never trust the client's rate: 0 divides by zero, a huge one turns the 32 s cap
            # into an unbounded buffer. The Spot sends 16 kHz; anything else falls back to it.
            try:
                rate = int(data.get("rate", 16000))
            except (TypeError, ValueError):
                rate = 16000
            self.rate = rate if 8000 <= rate <= 48000 else 16000
            self.active = True
            self.started = time.time()
            title, proc = typer.focused_window()
            await self.reply(op="focus", window=title)
        elif op == "cancel":
            self.active = False
            self.buf = bytearray()
        elif op == "stop":
            self.active = False
            assistant_mode = bool(data.get("assistant"))
            pcm = bytes(self.buf)
            self.buf = bytearray()
            secs = len(pcm) / 2 / self.rate
            print("voice: %.1fs of audio, transcribing with %s..." % (secs, backend()))
            try:   # keep the last capture for diagnosis
                with wave.open(config.data_path("last_capture.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.rate); w.writeframes(pcm)
            except Exception as e:  # noqa: BLE001
                print("voice: could not save capture:", e)
            if secs < 0.3:
                await self.reply(op="transcript", text="", target="")
                return
            t0 = time.time()
            try:
                text = await loop.run_in_executor(None, transcribe_pcm, pcm, self.rate)
            except Exception as e:  # noqa: BLE001
                print("voice: transcription failed:", e)
                await self.reply(op="error", text=str(e)[:200] or "transcription failed")
                return
            title, proc = typer.focused_window()
            print("voice: %.2fs -> %r  (focus: %s)" % (time.time() - t0, text, title))
            if text and COMMAND_HANDLER is not None:
                try:
                    result = await COMMAND_HANDLER(text)
                except Exception as e:  # noqa: BLE001
                    print("voice: command failed:", e)
                    result = None
                if result:
                    await self.reply(op="command", text=result.get("text", "done"), target=title)
                    return
            if text and assistant_mode and ASSISTANT_HANDLER is not None:
                try:
                    result = await ASSISTANT_HANDLER(text, self.device)
                except Exception as e:  # noqa: BLE001
                    print("voice: assistant failed:", e)
                    result = None
                if result is not None:
                    await self.reply(op="assistant", text=result.get("text", ""), target=title)
                    return
            await self.reply(op="transcript", text=text, target=title)
        elif op == "send":
            text = data.get("text", "")
            enter = bool(data.get("enter", True))
            target = data.get("target", "focused")
            res = await loop.run_in_executor(None, typer.type_text, text, enter, target)
            print("voice: typed into %r (%s) enter=%s ok=%s" % (res["window"], res["process"], enter, res["ok"]))
            if res["ok"]:
                await self.reply(op="sent", window=res["window"])
            else:
                await self.reply(op="error", text=res["error"] or "typing failed")
