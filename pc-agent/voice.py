"""Voice channel for the EchoPortal agent: receives PCM from the Spot, transcribes with Whisper, types on the PC.

Protocol (channel "voice"):
  Spot -> PC   {"op":"start","rate":16000}   then binary frames of 16-bit mono PCM   then {"op":"stop"} | {"op":"cancel"}
  PC -> Spot   {"op":"transcript","text":..., "target": "<focused window title>"}
  Spot -> PC   {"op":"send","text":..., "enter":true|false, "target":"focused"|"claude"}
  PC -> Spot   {"op":"sent","window":...} | {"op":"error","text":...}
"""
import json
import threading
import time
import wave

import numpy as np

import typer

MODEL_NAME = "large-v3-turbo"
_model = None
_model_lock = threading.Lock()
_device_used = "?"


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


def transcribe_pcm(pcm: bytes, rate: int) -> str:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != 16000:
        # crude resample; the Spot sends 16 kHz so this is only a safety net
        idx = np.arange(0, len(audio), rate / 16000.0)
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    # The Spot's mic array is very quiet (~-34 dBFS peaks); normalise so Whisper's VAD sees normal levels.
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if 0 < peak < 0.3:
        audio = audio * (0.5 / peak)
    m = load_model()
    segs, _ = m.transcribe(audio, beam_size=5, language="en", vad_filter=True,
                           condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


class VoiceSession:
    """Per-WebSocket-client state."""

    def __init__(self, ws):
        self.ws = ws
        self.buf = bytearray()
        self.rate = 16000
        self.active = False
        self.started = 0.0
        self.frames = 0

    async def reply(self, **data):
        await self.ws.send(json.dumps({"ch": "voice", "data": data}))

    def on_binary(self, frame: bytes):
        if self.active:
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
            self.rate = int(data.get("rate", 16000))
            self.active = True
            self.started = time.time()
            title, proc = typer.focused_window()
            await self.reply(op="focus", window=title)
        elif op == "cancel":
            self.active = False
            self.buf = bytearray()
        elif op == "stop":
            self.active = False
            pcm = bytes(self.buf)
            self.buf = bytearray()
            secs = len(pcm) / 2 / self.rate
            print("voice: %.1fs of audio, transcribing..." % secs)
            try:   # keep the last capture for diagnosis
                with wave.open("last_capture.wav", "wb") as w:
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
                await self.reply(op="error", text="transcription failed")
                return
            title, proc = typer.focused_window()
            print("voice: %.2fs -> %r  (focus: %s)" % (time.time() - t0, text, title))
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
