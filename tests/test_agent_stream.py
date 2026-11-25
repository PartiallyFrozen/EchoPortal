"""Agent-level reply fan-out: sentence clips are keyed by device, not by turn token."""
import asyncio
import json
from types import SimpleNamespace

import agent
import config


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


def test_supplied_device_reads_aiohttp_query_string():
    ws = SimpleNamespace(
        request=SimpleNamespace(path="/", path_qs="/?device=spot-42", raw_path="/"),
        path="/",
    )
    assert agent._supplied_device(ws) == "spot-42"

    old = SimpleNamespace(path="/?device=spot-7")
    assert agent._supplied_device(old) == "spot-7"


def test_streamed_sentence_clips_are_collected_and_published(monkeypatch):
    sent = []

    async def fake_broadcast(channel, data):
        sent.append((channel, data))

    clips = iter(("tts/one.wav", "tts/two.wav"))
    monkeypatch.setattr(agent, "_broadcast", fake_broadcast)
    monkeypatch.setattr(agent.config, "VALUES", {**config.VALUES,
        "assistant_speak": True, "assistant_stream_tts": True})
    monkeypatch.setattr(agent.tts, "speak", lambda text, device="": next(clips))
    cleanup = []
    monkeypatch.setattr(agent.tts, "cleanup", lambda: cleanup.append(True))

    async def scenario():
        agent.STREAM_AUDIO.pop("spot-1", None)
        event = {"op": "speak", "text": "First sentence.", "token": "turn-1", "device": "spot-1"}
        await agent._assistant_emit(event)
        # A tool round gives this turn a new token; clips must still belong to the device.
        event = {"op": "speak", "text": "Second sentence.", "token": "turn-2", "device": "spot-1"}
        await agent._assistant_emit(event)
        assert agent.STREAM_AUDIO["spot-1"] == ["tts/one.wav", "tts/two.wav"]

        result = await agent._finish_assistant({
            "op": "reply", "text": "First sentence. Second sentence.",
            "token": "turn-2", "device": "spot-1",
        })

        assert result["audio"] == "tts/one.wav"
        assert result["audio_segments"] == ["tts/one.wav", "tts/two.wav"]
        assert result["streamed_audio"] is True
        assert "spot-1" not in agent.STREAM_AUDIO
        assert cleanup == [True]

    asyncio.run(scenario())
    assert any(data.get("segment") is True for _channel, data in sent)
    assert any(data.get("op") == "reply" for _channel, data in sent)


def test_full_reply_audio_is_used_without_streamed_clips(monkeypatch):
    sent = []

    async def fake_broadcast(channel, data):
        sent.append((channel, data))

    monkeypatch.setattr(agent, "_broadcast", fake_broadcast)
    monkeypatch.setattr(agent.config, "VALUES", {**config.VALUES,
        "assistant_speak": True, "assistant_stream_tts": False})
    monkeypatch.setattr(agent.tts, "speak", lambda text, device="": "tts/full.wav")
    monkeypatch.setattr(agent.tts, "cleanup", lambda: None)

    async def scenario():
        agent.STREAM_AUDIO.pop("spot-2", None)
        result = await agent._finish_assistant({
            "op": "reply", "text": "All at once.", "token": "turn-3", "device": "spot-2",
        })
        assert result["audio"] == "tts/full.wav"
        assert "audio_segments" not in result and "streamed_audio" not in result

    asyncio.run(scenario())


def test_assistant_events_are_not_sent_to_another_spot(monkeypatch):
    asking, other, dashboard = FakeClient(), FakeClient(), FakeClient()
    monkeypatch.setattr(agent, "CLIENTS", {asking, other, dashboard})
    monkeypatch.setattr(agent, "WS_DEVICES", {asking: "spot-a", other: "spot-b", dashboard: ""})

    async def scenario():
        await agent._broadcast("assistant", {"op": "reply", "device": "spot-a", "text": "private"})

    asyncio.run(scenario())
    assert json.loads(asking.sent[0])["data"]["text"] == "private"
    assert dashboard.sent and json.loads(dashboard.sent[0])["data"]["text"] == "private"
    assert other.sent == []


def test_stream_reset_discards_partial_clips_before_fallback(monkeypatch):
    sent = []

    async def fake_broadcast(channel, data):
        sent.append((channel, data))

    clips = iter(("tts/partial.wav", "tts/full.wav"))
    monkeypatch.setattr(agent, "_broadcast", fake_broadcast)
    monkeypatch.setattr(agent.config, "VALUES", {**config.VALUES,
        "assistant_speak": True, "assistant_stream_tts": True})
    monkeypatch.setattr(agent.tts, "speak", lambda text, device="": next(clips))
    monkeypatch.setattr(agent.tts, "cleanup", lambda: None)

    async def scenario():
        agent.STREAM_AUDIO.pop("spot-3", None)
        await agent._assistant_emit({"op": "speak", "text": "Partial.", "token": "bad",
                                     "device": "spot-3"})
        assert agent.STREAM_AUDIO["spot-3"] == ["tts/partial.wav"]
        await agent._assistant_emit({"op": "stream_reset", "token": "bad", "device": "spot-3"})
        assert "spot-3" not in agent.STREAM_AUDIO
        result = await agent._finish_assistant({"op": "reply", "text": "Full answer.",
                                                "token": "good", "device": "spot-3"})
        assert result["audio"] == "tts/full.wav"
        assert "audio_segments" not in result

    asyncio.run(scenario())
    assert any(data.get("op") == "stream_reset" for _channel, data in sent)


def test_empty_api_token_is_generated(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    values = {**config.VALUES, "api_token": ""}
    monkeypatch.setattr(config, "CONFIG", str(path))
    monkeypatch.setattr(config, "VALUES", values)

    token = config.ensure_token()

    assert token and token == values["api_token"]
    assert json.loads(path.read_text(encoding="utf-8"))["api_token"] == token
