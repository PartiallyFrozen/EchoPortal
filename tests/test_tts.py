"""Offline Jarvis voice style: preset settings, voice choice and the WAV pitch shift.

No pywin32 is imported at module load; the COM objects are stubbed so these tests run anywhere.
"""
import sys
import types
import wave

import numpy as np

import config
import tts


class Token:
    def __init__(self, name, language, gender):
        self.name = name
        self.language = language
        self.gender = gender

    def GetDescription(self):
        return self.name

    def GetAttribute(self, key):
        return {"Language": self.language, "Gender": self.gender}.get(key, "")


TOKENS = [
    Token("Microsoft David - English (United States)", "409", "Male"),
    Token("Microsoft George - English (United Kingdom)", "809", "Male"),
    Token("Microsoft Hazel - English (United Kingdom)", "809", "Female"),
]


class Engine:
    def __init__(self):
        self.Rate = 0
        self.Volume = 100
        self.Voice = None
        self.spoken = []

    def GetVoices(self):
        return TOKENS

    def Speak(self, text):
        self.spoken.append(text)


class Stream:
    def Open(self, path, mode):
        self.path = path
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x00\x00" * 1600)

    def Close(self):
        pass


def _stub_com(monkeypatch, engine):
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    client = types.ModuleType("win32com.client")
    client.Dispatch = lambda name: engine if name == "SAPI.SpVoice" else Stream()
    win32com = types.ModuleType("win32com")
    win32com.client = client
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


def test_style_settings_default_to_jarvis(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {})
    settings = tts.style_settings()

    assert settings["style"] == "jarvis"
    assert settings["rate"] == 0
    assert settings["pitch"] == 0
    assert settings["volume"] == 100
    assert settings["language"] == "809"


def test_style_settings_honor_overrides_and_reject_garbage(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {
        "tts_style": "jarvis", "tts_rate": 4, "tts_pitch": "oops", "tts_volume": 500,
    })
    settings = tts.style_settings()

    assert settings["rate"] == 4
    assert settings["pitch"] == 0  # invalid override falls back to the preset
    assert settings["volume"] == 100  # clamped


def test_unknown_style_is_natural(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {"tts_style": "robot"})
    assert tts.style_settings()["style"] == "natural"
    assert tts.style_settings()["pitch"] == 0


def test_pick_voice_prefers_english_male(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {"tts_voice": ""})
    chosen = tts._pick_voice(Engine(), tts.style_settings())

    assert chosen is TOKENS[1]  # en-GB male, not the US default or the en-GB woman


def test_pick_voice_honors_exact_name(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {"tts_voice": "David"})
    assert tts._pick_voice(Engine(), tts.style_settings()) is TOKENS[0]


def test_pick_voice_with_no_style_preference_uses_default(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {"tts_style": "natural"})
    assert tts._pick_voice(Engine(), tts.style_settings()) is None


def _tone(path, frequency=220, seconds=1.0):
    rate = 16000
    values = (np.sin(2 * np.pi * frequency * np.arange(int(rate * seconds)) / rate) * 12000).astype(np.int16)
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(values.tobytes())


def _dominant(path):
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
        values = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(float)
        duration = len(values) / rate
    spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values))))
    return float(np.fft.rfftfreq(len(values), 1 / rate)[np.argmax(spectrum)]), duration


def test_pitch_shift_deepens_and_lengthens(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {})
    source = str(tmp_path / "source.wav")
    _tone(source)
    before, before_duration = _dominant(source)

    tts._shift_pitch(source, -4)
    after, after_duration = _dominant(source)

    assert after < before * 0.9
    assert after_duration > before_duration


def test_speak_applies_style_and_returns_media_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {"tts_backend": "sapi"})
    monkeypatch.setattr(tts, "_FOLDER", str(tmp_path))
    monkeypatch.setattr(tts, "_shift_pitch", lambda path, semitones: None)
    engine = Engine()
    _stub_com(monkeypatch, engine)

    result = tts.speak(" Certainly, sir. ")

    assert result.startswith("tts/") and result.endswith(".wav")
    assert engine.spoken == ["Certainly, sir."]
    assert engine.Rate == 0 and engine.Volume == 100
    assert engine.Voice is TOKENS[1]


def test_style_settings_apply_per_device_overrides(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {
        "tts_style": "jarvis",
        "tts_rate": 0,
        "assistant_devices": {"spot-1": {
            "tts_style": "natural", "tts_rate": 3, "tts_pitch": -2,
            "tts_length_scale": "1.25", "tts_voice": "David",
        }},
    })

    settings = tts.style_settings("spot-1")

    assert settings["style"] == "natural"
    assert settings["rate"] == 3 and settings["pitch"] == -2
    assert settings["voice"] == "David" and settings["length_scale"] == 1.25
    assert tts.style_settings("other")["rate"] == 0


def test_speak_strips_pause_markers(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {"tts_backend": "sapi", "assistant_pause_marker": "[pause]"})
    monkeypatch.setattr(tts, "_FOLDER", str(tmp_path))
    monkeypatch.setattr(tts, "_shift_pitch", lambda path, semitones: None)
    engine = Engine()
    _stub_com(monkeypatch, engine)

    tts.speak("Wait [pause] then go.")

    assert engine.spoken == ["Wait then go."]


def test_speak_returns_none_for_empty_text(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_FOLDER", str(tmp_path))
    assert tts.speak("   ") is None


def test_piper_info_detects_runtime_and_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {})
    monkeypatch.setattr(config, "FROZEN", True)
    monkeypatch.setattr(config, "data_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    runtime = tmp_path / "piper"
    runtime.mkdir()
    (runtime / "piper.exe").write_bytes(b"exe")
    (runtime / tts.DEFAULT_MODEL).write_bytes(b"model")

    info = tts.piper_info()

    assert info["available"] is True
    assert info["folder"] == str(runtime)
    assert info["model"] == tts.DEFAULT_MODEL


def test_piper_info_ignores_unsafe_model_names(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {"tts_piper_model": "../../config.json"})
    monkeypatch.setattr(config, "FROZEN", True)
    monkeypatch.setattr(config, "data_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    runtime = tmp_path / "piper"
    runtime.mkdir()
    (runtime / "piper.exe").write_bytes(b"exe")

    info = tts.piper_info()

    assert info["model"] == tts.DEFAULT_MODEL
    assert info["available"] is False


def test_speak_prefers_piper_over_sapi(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {})
    monkeypatch.setattr(tts, "_FOLDER", str(tmp_path))
    rendered = []

    def fake_piper(text, path):
        rendered.append(text)
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x00\x00" * 1600)
        return True

    monkeypatch.setattr(tts, "_piper_speak", fake_piper)
    engine = Engine()
    _stub_com(monkeypatch, engine)

    result = tts.speak("Hello there")

    assert result.startswith("tts/")
    assert rendered == ["Hello there"]
    assert engine.spoken == []  # SAPI was not touched


def test_piper_render_passes_length_scale(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {"tts_piper_length_scale": 1.25})
    monkeypatch.setattr(tts, "piper_info", lambda: {
        "available": True, "folder": str(tmp_path), "model": "voice.onnx",
    })
    seen = []

    class Proc:
        returncode = 0
        stderr = b""

    def fake_run(command, **kwargs):
        seen.append(command)
        output = command[command.index("--output_file") + 1]
        with wave.open(output, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x00\x00" * 1600)
        return Proc()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    assert tts._piper_speak("Hello", str(tmp_path / "out.wav")) is True
    assert seen[0][seen[0].index("--length_scale") + 1] == "1.25"
