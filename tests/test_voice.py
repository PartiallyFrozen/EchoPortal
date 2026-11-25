"""Voice transcription: backend selection, WAV framing, and the Gemini request/response."""
import asyncio
import base64
import io
import json
import wave

import numpy as np
import pytest

import voice


def _values(monkeypatch, **overrides):
    values = {
        "voice_backend": "auto",
        "gemini_api_key": "",
        "gemini_model": "gemini-flash-lite-latest",
    }
    values.update(overrides)
    monkeypatch.setattr(voice.config, "VALUES", values)


def test_auto_uses_gemini_when_a_key_is_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _values(monkeypatch, gemini_api_key="test-key")
    assert voice.backend() == "gemini"


def test_auto_falls_back_to_whisper_without_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _values(monkeypatch)
    assert voice.backend() == "whisper"


def test_explicit_backend_wins(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _values(monkeypatch, voice_backend="whisper", gemini_api_key="test-key")
    assert voice.backend() == "whisper"


def test_environment_key_overrides_config(monkeypatch):
    _values(monkeypatch, gemini_api_key="from-config")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert voice.gemini_key() == "from-env"


def test_status_warns_when_gemini_has_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _values(monkeypatch, voice_backend="gemini")
    assert "NO API KEY" in voice.status()


def test_prepare_normalises_a_quiet_clip():
    quiet = (np.ones(1600, dtype=np.int16) * 300).tobytes()
    audio = voice.prepare(quiet, 16000)
    assert len(audio) == 1600
    assert 0.45 <= float(np.abs(audio).max()) <= 0.55


def test_prepare_treats_silence_as_empty():
    assert voice.prepare(np.zeros(16000, dtype=np.int16).tobytes(), 16000).size == 0


def test_prepare_treats_a_quiet_noise_floor_as_empty():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 20, 16000).astype(np.int16).tobytes()
    assert voice.prepare(noise, 16000).size == 0


def test_prepare_caps_the_gain():
    # peak 0.0067 would need 75x to reach 0.5; the cap keeps it below full scale.
    quiet = (np.ones(1600, dtype=np.int16) * 220).tobytes()
    audio = voice.prepare(quiet, 16000)
    assert audio.size
    assert float(np.abs(audio).max()) <= 0.5


def test_transcribe_skips_the_api_for_silence(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _values(monkeypatch, gemini_api_key="test-key")
    called = []
    monkeypatch.setattr(voice, "gemini_transcribe", lambda *a, **k: called.append(1) or "x")
    assert voice.transcribe_pcm(np.zeros(16000, dtype=np.int16).tobytes(), 16000) == ""
    assert not called


def test_voice_session_caps_the_buffer():
    class WS:
        async def send(self, *_args):
            pass

    session = voice.VoiceSession(WS())
    session.active = True
    session.rate = 16000
    session.on_binary(b"\x01\x00" * (16000 * 40))   # 40 s in a single frame
    assert len(session.buf) == int(16000 * 2 * voice.MAX_UTTERANCE_SECS)
    assert session.overflowed


def test_voice_session_drops_an_odd_trailing_byte():
    class WS:
        async def send(self, *_args):
            pass

    session = voice.VoiceSession(WS())
    session.active = True
    session.rate = 16000
    session.on_binary(b"\x01\x00\x02")   # 1.5 samples
    assert len(session.buf) == 2


def test_voice_session_ignores_a_bad_client_rate(monkeypatch):
    monkeypatch.setattr(voice.typer, "focused_window", lambda: ("", ""))

    class WS:
        async def send(self, *_args):
            pass

    for supplied in (0, -1, 10**9, "fast"):
        session = voice.VoiceSession(WS())
        asyncio.run(session.on_message({"op": "start", "rate": supplied}, None))
        assert session.rate == 16000, supplied


def test_prepare_tolerates_an_odd_byte_count():
    # A loud sample plus a stray byte: frombuffer would raise before the trim.
    audio = voice.prepare(b"\x00\x40\x00", 16000)
    assert audio.size == 1


def test_wav_bytes_is_mono_16_bit():
    audio = np.zeros(800, dtype=np.float32)
    raw = voice.wav_bytes(audio, 16000)
    assert raw[:4] == b"RIFF"
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 800


def test_parse_gemini_joins_parts():
    payload = {"candidates": [{"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}]}
    assert voice.parse_gemini(payload) == "hello world"


def test_parse_gemini_reports_a_block_reason():
    with pytest.raises(RuntimeError, match="blocked"):
        voice.parse_gemini({"promptFeedback": {"blockReason": "SAFETY"}})


def test_parse_gemini_reports_an_api_error():
    with pytest.raises(RuntimeError, match="quota"):
        voice.parse_gemini({"error": {"message": "quota exceeded"}})


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *_args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_gemini_transcribe_posts_an_inline_wav(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _values(monkeypatch, gemini_model="gemini-test")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data)
        seen["timeout"] = timeout
        return _Response({"candidates": [{"content": {"parts": [{"text": "hello there"}]}}]})

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)

    audio = np.zeros(1600, dtype=np.float32)
    assert voice.gemini_transcribe(audio) == "hello there"

    assert "gemini-test" in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "test-key"
    assert seen["timeout"] == 45.0
    inline = seen["body"]["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "audio/wav"
    assert base64.b64decode(inline["data"])[:4] == b"RIFF"


def test_gemini_transcribe_requires_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _values(monkeypatch)
    with pytest.raises(RuntimeError, match="no Gemini API key"):
        voice.gemini_transcribe(np.zeros(100, dtype=np.float32))


def _http_error(req, code, message):
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    return voice.urllib.error.HTTPError(req.full_url, code, message, None, io.BytesIO(body))


def test_gemini_retries_once_on_503(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _values(monkeypatch, gemini_api_key="test-key")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(req, 503, "high demand")
        return _Response({"candidates": [{"content": {"parts": [{"text": "after retry"}]}}]})

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(voice.time, "sleep", lambda _s: None)
    assert voice.gemini_transcribe(np.zeros(1600, dtype=np.float32)) == "after retry"
    assert len(calls) == 2


def test_gemini_does_not_retry_a_bad_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _values(monkeypatch, gemini_api_key="test-key")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise _http_error(req, 400, "invalid argument")

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="400"):
        voice.gemini_transcribe(np.zeros(1600, dtype=np.float32))
    assert len(calls) == 1
