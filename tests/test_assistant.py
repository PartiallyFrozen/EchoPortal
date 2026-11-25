"""Jarvis assistant: tool declarations, note search, model loop and confirmation flow.

Everything here is pure logic: the model call is replaced with a stub and no tool touches the
desktop, the network or an LLM. The same restrictions as the other tests apply - no pywin32.
"""
import asyncio
import io
import json
import sys
import time
import urllib.error

import pytest

import assistant
import config
import robot


@pytest.fixture(autouse=True)
def no_confirm_audio(monkeypatch):
    """Keep confirmation tests offline; SAPI audio is exercised by the installed agent."""
    monkeypatch.setattr(assistant, "_confirm_audio", lambda text: None)


def test_system_prompt_follows_the_voice_style(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {"tts_style": "jarvis"})
    jarvis = assistant.system_prompt()
    assert "understated" in jarvis
    assert "fictional character" in jarvis

    monkeypatch.setattr(config, "VALUES", {"tts_style": "natural"})
    plain = assistant.system_prompt()
    assert plain.startswith(assistant.SYSTEM_PROMPT)
    assert "Current local time:" in plain
    assert "unverified background" in assistant.SYSTEM_PROMPT
    assert "Never invent action names" in assistant.SYSTEM_PROMPT


def test_clock_answer_matches_common_questions_without_calling_the_model():
    for question in (
        "What time is it?",
        "what's the time",
        "tell me the date",
        "What day is it?",
        "today's date",
        "what's the current time",
        "what is today's date",
        "what is the current date",
        "what time is it now",
        "what's the time right now",
        "tell me today's date",
    ):
        assert assistant.clock_answer(question).startswith("It's ")

    assert assistant.clock_answer("What's on my calendar today?") == ""
    assert assistant.clock_answer("Set a timer for one minute.") == ""


def test_tool_declarations_are_complete():
    names = [t["name"] for t in assistant.TOOLS]
    assert len(names) == len(set(names)), "a duplicate name would make the model call ambiguous"
    for tool in assistant.TOOLS:
        assert tool["description"]
        assert isinstance(tool["parameters"], dict)
        schema = assistant._schema(tool["name"])
        assert schema["name"] == tool["name"]
        assert set(schema["parameters"]["properties"]) == set(tool["parameters"])
        assert schema["parameters"]["required"] == list(tool["parameters"])

    assert assistant.CONFIRM_TOOLS == {
        "type_text", "open_app", "run_command", "robot", "forget", "look_at_screen", "ha_call",
        "task_add", "calendar_add", "window_focus", "window_close", "media_control",
    }
    assert assistant._schema("type_text")["parameters"]["properties"]["enter"]["type"] == "boolean"


def test_routing_hint_targets_clear_intents():
    available = set(assistant.TOOL_BY_NAME) - {"robot"}
    cases = {
        "What is on my calendar today?": "calendar",
        "Search my notes for the Sabres jersey schedule.": "search_notes",
        "When is the Sabres season opener?": "sabres_schedule",
        "Search the web for the official Sabres announcement.": "web_search",
        "Fetch https://example.com and summarise it.": "web_fetch",
        "How much VRAM does my graphics card have?": "pc_stats",
        "What did I say I drink?": "recall",
        "Pause the study timer.": "timer",
        "List my open windows.": "window_list",
        "Run the command dir in a terminal.": "run_command",
    }
    for question, expected in cases.items():
        hint = assistant.routing_hint(question, available)
        assert "`%s`" % expected in hint, question

    assert assistant.routing_hint("What is the capital of Japan?", available) == ""
    robot_hint = assistant.routing_hint("Start the robot vacuum.", available)
    assert "Say plainly that the robot vacuum is not configured" in robot_hint


def test_raw_json_tool_text_is_recovered():
    available = {"timer", "recall"}
    calls, remainder = assistant.calls_from_text(
        '{"name": "timer", "arguments": {"text": "Cancel the kitchen timer."}}', available)
    assert calls == [{"name": "timer", "args": {"text": "Cancel the kitchen timer."}}]
    assert remainder == ""

    calls, remainder = assistant.calls_from_text(
        'I think you mean {"name": "recall", "args": {"query": "my car"}}.', available)
    assert calls == [{"name": "recall", "args": {"query": "my car"}}]

    # Ordinary prose and undeclared tools stay as text.
    assert assistant.calls_from_text("Your parking spot is B2.", available) == ([], "Your parking spot is B2.")
    assert assistant.calls_from_text('{"name": "robot", "args": {}}', available)[0] == []


def test_required_tool_comes_from_hint():
    hint = assistant.routing_hint("What is my parking spot?", set(assistant.TOOL_BY_NAME))
    assert assistant.required_tool(hint) == "recall"
    assert assistant.required_tool("plain answer") == ""


def test_sabres_answer_uses_tool_summary(monkeypatch):
    rounds = []

    async def fake_model(self, messages, *args, **kwargs):
        rounds.append(messages)
        assert len(rounds) == 1, "the authoritative summary should end the turn"
        return {"text": "", "calls": [{"name": "sabres_schedule",
                                        "args": {"query": "season opener"}}]}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    summary = ("Thursday, 2026-10-01: Sabres visit Columbus Blue Jackets at Nationwide Arena, "
               "7:00 PM EDT, wearing Road White; TV: MSG-B.")
    engine = assistant.AssistantEngine({"sabres_schedule": lambda query="": {
        "ok": True, "summary": summary}})
    result = asyncio.run(engine.ask("When is the Sabres season opener?"))

    assert result["trace"] == ["sabres_schedule"]
    assert result["text"] == summary


def test_remember_is_called_when_the_model_skips_it_twice(monkeypatch):
    answers = [
        {"text": "Your parking spot is B2.", "calls": []},
        {"text": "Your parking spot is B2.", "calls": []},
        {"text": "Saved.", "calls": []},
    ]
    saved = []

    async def fake_model(self, messages, *args, **kwargs):
        return answers.pop(0)

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({"remember": lambda text="": saved.append(text) or {"ok": True}})

    result = asyncio.run(engine.ask("Remember that my parking spot is B2."))

    assert result["trace"] == ["remember"]
    assert saved == ["my parking spot is B2"]


def test_vram_answer_uses_tool_value(monkeypatch):
    async def fake_model(self, messages, *args, **kwargs):
        return {"text": "I don't have that information.",
                "calls": [{"name": "pc_stats", "args": {}}]}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({"pc_stats": lambda: {"ok": True, "vram_gb": 16.0}})

    result = asyncio.run(engine.ask("How much VRAM does my graphics card have?"))

    assert result["trace"] == ["pc_stats"]
    assert result["text"] == "Your graphics card has 16 GB of VRAM."


def test_read_only_fallback_arguments():
    assert assistant.synthetic_args("recall", "What is my parking spot?") == {
        "query": "What is my parking spot?"}
    assert assistant.synthetic_args("calendar", "What is on today?") == {}
    assert assistant.synthetic_args("web_fetch", "Fetch https://example.com/a?b=1.") == {
        "url": "https://example.com/a?b=1"}
    assert assistant.synthetic_args("remember", "Remember that my seat is 4A.") == {
        "text": "my seat is 4A"}
    assert assistant.synthetic_args("timer", "Cancel the timer.") is None


def test_safe_filename_stays_inside_the_drafts_folder():
    assert assistant._safe_filename("notes") == "notes.md"
    assert assistant._safe_filename("../../evil.txt") == "evil.txt.md"
    name = assistant._safe_filename("../a/b/<draft>.md")
    assert "/" not in name and "\\" not in name
    assert name.endswith(".md")


def test_search_notes_reads_configured_folders(monkeypatch, tmp_path):
    (tmp_path / "a.md").write_text("EchoPortal weather face notes", encoding="utf-8")
    (tmp_path / "b.txt").write_text("something unrelated", encoding="utf-8")
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_knowledge_dirs": [str(tmp_path)]})

    hits = assistant.search_notes("weather")

    assert hits
    assert hits[0]["path"].endswith("a.md")
    assert "weather" in hits[0]["snippet"].lower()


def test_search_notes_ignores_empty_queries(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_knowledge_dirs": [str(tmp_path)]})
    assert assistant.search_notes("  .. ") == []


def test_local_endpoint_is_not_appended_twice(monkeypatch):
    seen = {}

    def fake_post(self, url, body, headers, timeout):
        seen["url"] = url
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_local_url": "http://localhost:11434/api/chat/"})
    monkeypatch.setattr(assistant.AssistantEngine, "_post", fake_post)
    assistant.AssistantEngine({})._local([{"role": "user", "content": "hi"}])
    assert seen["url"] == "http://localhost:11434/api/chat"


def test_local_retries_after_ollama_repeat_limit(monkeypatch):
    """A stuck Qwen runner is unloaded and the turn is retried once."""
    seen = []
    unloads = []

    def fake_post(self, url, body, headers, timeout):
        seen.append(body)
        if len(seen) == 1:
            payload = b'{"error":"prediction aborted, token repeat limit reached"}'
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(payload))
        return {"message": {"content": "recovered", "tool_calls": []}}

    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_local_url": "http://localhost:11434/api/chat",
        "assistant_local_model": assistant.DEFAULT_LOCAL_MODEL,
        "assistant_model_choice": "local:" + assistant.DEFAULT_LOCAL_MODEL})
    monkeypatch.setattr(assistant.AssistantEngine, "_post", fake_post)
    monkeypatch.setattr(assistant, "_unload_local_model",
                        lambda base, model: unloads.append((base, model)) or True)

    result = assistant.AssistantEngine({})._local([{"role": "user", "content": "hi"}])

    assert result["text"] == "recovered"
    assert len(seen) == 2
    assert unloads == [("http://localhost:11434/api/chat", assistant.DEFAULT_LOCAL_MODEL)]


def test_ask_replies_and_emits_progress(monkeypatch):
    async def fake_model(self, messages, *args, **kwargs):
        return {"text": "hello from Jarvis", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    events = []
    engine = assistant.AssistantEngine({}, broadcast=events.append)

    result = asyncio.run(engine.ask("hi"))

    assert result["op"] == "reply"
    assert result["text"] == "hello from Jarvis"
    assert {"op": "prompt", "text": "hi", "token": result["token"], "device": ""} in events
    assert {"op": "thinking", "text": "", "token": result["token"], "device": ""} in events
    assert engine.history[-1] == {"role": "assistant", "content": "hello from Jarvis"}


def test_gemini_stream_keeps_spaces_between_deltas(monkeypatch):
    """Gemini streams 'VOICE' then ' CHECK'; stripping each delta used to join the words."""
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "gemini_api_key": "test-key"})
    payloads = [
        {"candidates": [{"content": {"parts": [{"text": "VOICE"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": " CHECK"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": ""}]}}]},
    ]

    def fake_post_sse(self, *args, **kwargs):
        return {"payloads": payloads}

    monkeypatch.setattr(assistant.AssistantEngine, "_post_sse", fake_post_sse)

    result = assistant.AssistantEngine({})._gemini(
        [{"role": "user", "content": "hi"}], stream=True)

    assert result["text"] == "VOICE CHECK"


def test_parts_result_strips_only_completed_text():
    text, _ = assistant.AssistantEngine._parts_result([{"text": "  hello  "}])
    delta, _ = assistant.AssistantEngine._parts_result([{"text": " CHECK"}], strip=False)
    assert text == "hello"
    assert delta == " CHECK"


def test_model_picker_choice_overrides_topic_routing(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_backend": "hybrid",
        "assistant_model_choice": "local:custom:7b",
        "assistant_route_web": "gemini"})
    engine = assistant.AssistantEngine({})
    messages = [{"role": "tool", "name": "web_search", "content": "ok"}]

    assert engine._route(messages, "search") == "local"
    assert engine.status()["backend"] == "local"
    assert engine.status()["model"] == "custom:7b"


def test_model_picker_persists_only_assistant_settings(monkeypatch):
    monkeypatch.setattr(config, "VALUES", dict(config.VALUES))
    saves = []
    monkeypatch.setattr(config, "save", lambda: saves.append(True))

    result = assistant.set_model_choice("gemini:custom-flash")

    assert result["ok"] is True
    assert config.VALUES["assistant_model_choice"] == "gemini:custom-flash"
    assert config.VALUES["assistant_backend"] == "gemini"
    assert config.VALUES["assistant_gemini_model"] == "custom-flash"
    assert saves == [True]
    assert assistant.set_model_choice("bad-id")["ok"] is False


def test_local_calls_disable_thinking(monkeypatch):
    seen = {}

    def fake_post(self, url, body, headers, timeout):
        seen.update(body)
        return {"message": {"content": "done", "tool_calls": []}}

    monkeypatch.setattr(assistant.AssistantEngine, "_post", fake_post)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_local_model": assistant.DEFAULT_LOCAL_MODEL,
        "assistant_model_choice": ""})

    result = assistant.AssistantEngine({})._local([{"role": "user", "content": "hi"}])

    assert result["text"] == "done"
    assert seen["think"] is False
    assert seen["model"] == assistant.DEFAULT_LOCAL_MODEL


def test_confirmation_runs_a_tool_only_when_approved(monkeypatch):
    ran = []
    state = {"turns": 0}

    async def fake_model(self, messages, *args, **kwargs):
        state["turns"] += 1
        if state["turns"] % 2:
            return {"text": "", "calls": [{"name": "type_text", "args": {"text": "hello", "enter": True}}]}
        return {"text": "done", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({"type_text": lambda text, enter=True: ran.append((text, enter))})

    first = asyncio.run(engine.ask("type hello"))
    assert first["op"] == "confirm"
    assert first["tool"] == "type_text"

    declined = asyncio.run(engine.confirm(first["token"], False))
    assert declined["op"] == "reply"
    assert ran == [], "declining must not execute the tool"

    second = asyncio.run(engine.ask("type hello"))
    approved = asyncio.run(engine.confirm(second["token"], True))
    assert approved["op"] == "reply"
    assert ran == [("hello", True)]

    expired = asyncio.run(engine.confirm("not-a-token", True))
    assert expired["op"] == "error"


def test_confirmation_uses_structured_tool_messages_and_new_token(monkeypatch):
    seen = []
    ran = []

    async def fake_model(self, messages, *args, **kwargs):
        seen.append([dict(m) for m in messages])
        if len(seen) == 1:
            return {"text": "", "calls": [{"name": "type_text", "args": {"text": "hello", "enter": False}}]}
        return {"text": "Typed it.", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({
        "type_text": lambda text="", enter=False: ran.append(text) or {"ok": True},
    })

    pending = asyncio.run(engine.ask("type hello"))
    assert pending["op"] == "confirm"

    done = asyncio.run(engine.confirm(pending["token"], True))

    assert done["op"] == "reply"
    assert done["text"] == "Typed it."
    assert done["token"] != pending["token"], "resuming must not reuse the approval token"
    assert ran == ["hello"]
    assert len(seen) == 2, "the approved tool must go back to the model"

    assistant_msg = next(m for m in seen[1] if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant_msg["tool_calls"] == [{"name": "type_text", "args": {"text": "hello", "enter": False}}]

    tool_msg = next(m for m in seen[1] if m["role"] == "tool")
    assert tool_msg["name"] == "type_text"
    assert json.loads(tool_msg["content"])["result"] == {"ok": True}


def test_local_serializes_tool_calls_and_results_for_ollama(monkeypatch):
    bodies = []

    def fake_post(self, url, body, headers, timeout):
        bodies.append(body)
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(assistant.AssistantEngine, "_post", fake_post)
    engine = assistant.AssistantEngine({"timer": lambda text="": {"ok": True}})

    engine._local([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "set a timer"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"name": "timer", "args": {"text": "5 minutes"}}]},
        {"role": "tool", "name": "timer", "content": '{"ok": true}'},
    ])

    messages = bodies[0]["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[2]["tool_calls"] == [{
        "function": {"name": "timer", "arguments": {"text": "5 minutes"}},
    }]
    assert messages[3] == {"role": "tool", "content": '{"ok": true}'}


def test_unknown_and_disabled_tools_fail_closed(monkeypatch):
    async def unauthorized(self, messages, *args, **kwargs):
        return {"text": "", "calls": [{"name": "robot", "args": {"action": "start"}}]}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", unauthorized)
    engine = assistant.AssistantEngine({})
    result = asyncio.run(engine.ask("start the vacuum"))
    assert result["op"] == "reply", "a tool that was not injected is ignored by the loop"

    missing = asyncio.run(engine._run_tool({"name": "robot", "args": {"action": "start"}}))
    assert missing["ok"] is False
    assert "unknown tool" in missing["error"]

    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "robot_enabled": False})
    assert robot.RobotClient().configured() is False
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "robot_enabled": True,
                                           "robot_broker": "mqtt.example", "robot_topic": "home/{device_id}"})
    client = robot.RobotClient()
    assert client.configured() is True
    assert client.send("dance")["ok"] is False


def test_config_defaults_include_the_assistant(monkeypatch):
    assert config.DEFAULT["assistant_backend"] == "hybrid"
    assert "assistant" in config.DEFAULT["faces"]
    assert config.DEFAULT["assistant_speak"] is True


def test_auto_allow_skips_confirmation(monkeypatch):
    ran = []
    calls = []

    async def fake_model(self, messages, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"text": "", "calls": [{"name": "media_control", "args": {"action": "next"}}]}
        return {"text": "Skipped the prompt.", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    signature = assistant._call_signature({"name": "media_control", "args": {"action": "next"}})
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_auto_allow": {signature: True}})
    engine = assistant.AssistantEngine({"media_control": lambda action: ran.append(action) or {"ok": True}})

    result = asyncio.run(engine.ask("skip to the next track"))

    assert result["op"] == "reply"
    assert ran == ["next"]
    assert not engine.pending


def test_confirm_always_persists_the_exact_signature(monkeypatch):
    saved = {}

    async def fake_model(self, messages, *args, **kwargs):
        if getattr(fake_model, "n", 0) == 0:
            fake_model.n = 1
            return {"text": "", "calls": [{"name": "open_app", "args": {"name": "notepad"}}]}
        return {"text": "Opened it.", "calls": []}

    fake_model.n = 0
    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    monkeypatch.setattr(config, "set_value", lambda name, value: saved.update({name: value}))
    engine = assistant.AssistantEngine({"open_app": lambda name: {"ok": True, "app": name}})

    pending = asyncio.run(engine.ask("open notepad"))
    assert pending["op"] == "confirm"
    assert pending["expires_s"] > 0
    done = asyncio.run(engine.confirm(pending["token"], True, always=True))

    assert done["op"] == "reply"
    signature = assistant._call_signature({"name": "open_app", "args": {"name": "notepad"}})
    assert saved["assistant_auto_allow"][signature] is True


def test_confirmation_expires(monkeypatch):
    async def fake_model(self, messages, *args, **kwargs):
        return {"text": "", "calls": [{"name": "media_control", "args": {"action": "mute"}}]}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_confirm_timeout": 1})
    engine = assistant.AssistantEngine({"media_control": lambda action: {"ok": True}})

    pending = asyncio.run(engine.ask("mute"))
    engine.pending[pending["token"]]["created"] = time.time() - 10
    expired = asyncio.run(engine.confirm(pending["token"], True))

    assert expired["op"] == "error"
    assert "expired" in expired["text"]


def test_cancel_marks_a_turn_and_clears_pending():
    engine = assistant.AssistantEngine({})
    engine.pending["abc"] = {"tool": "media_control"}
    assert engine.cancel("abc") is True
    assert engine.is_cancelled("abc") is True
    assert "abc" not in engine.pending
    engine.cancel("")
    assert engine.is_cancelled("another-token") is True


def test_routing_follows_configured_rules(monkeypatch):
    engine = assistant.AssistantEngine({})
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_backend": "hybrid",
        "assistant_route_private": "local",
        "assistant_route_web": "gemini"})
    assert engine._route([{"role": "user", "content": "read my note"}], "read my note") == "local"
    web = [{"role": "tool", "name": "web_search", "content": "{}"}]
    assert engine._route(web, "what is the weather") == "gemini"
    vision = [{"role": "tool", "name": "look_at_screen", "content": "{}", "images": ["QUJD"]}]
    # "auto" means "follow the selected backend" rather than forcing a cloud call.
    assert engine._route(vision, "look") == engine._backend()


def test_restored_memory_turns_are_chat_messages(monkeypatch):
    class Store:
        def recent_turns(self, limit=6):
            return [{"id": 1, "user": "earlier question", "assistant": "earlier answer"}]

        def append_turn(self, user, assistant_text):
            pass

        def stats(self):
            return {}

    seen = []

    async def fake_model(self, messages, *args, **kwargs):
        seen.append([dict(m) for m in messages])
        return {"text": "ok", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({}, store=Store())

    result = asyncio.run(engine.ask("new question"))

    assert result["op"] == "reply"
    assert {"role": "user", "content": "earlier question"} in seen[0]
    marked = next(m for m in seen[0] if m["role"] == "assistant")
    assert marked["content"] == "Earlier answer (unverified, do not repeat): earlier answer"
    assert {"role": "user", "content": "new question"} in seen[0]


def test_metrics_history_and_device_snapshots():
    class FakeStore:
        def recent_turns(self, limit=6):
            return [{"id": i, "user": "u", "assistant": "a"} for i in range(limit)]

        def append_turn(self, user, assistant_text):
            self.last = (user, assistant_text)

        def stats(self):
            return {"facts": 1}

    store = FakeStore()
    engine = assistant.AssistantEngine({}, store=store)
    engine._record_metric("local", time.time() - 0.1, {"text": "ok", "calls": []})

    assert engine.metrics_snapshot(1)[0]["backend"] == "local"
    assert engine.history_snapshot(2)[0]["user"] == "u"
    assert engine.status()["memory"] == {"facts": 1}


def test_per_device_voice_style_changes_the_wording_prompt(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "tts_style": "jarvis",
        "assistant_devices": {"spot-1": {"tts_style": "natural"}},
    })

    assert assistant.effective_tts_style("spot-1") == "natural"
    natural = assistant.system_prompt("spot-1")
    jarvis = assistant.system_prompt("other")
    assert natural.startswith(assistant.SYSTEM_PROMPT)
    assert "understated" not in natural
    assert "understated" in jarvis


def test_device_preferences_win_over_the_global_picker(monkeypatch):
    monkeypatch.setattr(config, "set_value",
                        lambda name, value: config.VALUES.__setitem__(name, value))
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_backend": "local",
        "assistant_model_choice": "local:qwen3:14b",
        "assistant_devices": {},
    })

    saved = assistant.set_device_preferences("spot-1", {
        "model_choice": "gemini:gemini-2.5-flash",
        "tts_length_scale": "1.2",
        "tts_style": "natural",
    })

    assert saved["ok"] is True
    assert assistant.effective_model_choice("spot-1") == "gemini:gemini-2.5-flash"
    assert assistant.effective_model_choice("other-spot") == "local:qwen3:14b"
    engine = assistant.AssistantEngine({})
    assert engine._route([{"role": "user", "content": "hello"}], "hello", "spot-1") == "gemini"
    assert engine._route([{"role": "user", "content": "hello"}], "hello", "other-spot") == "local"
    assert assistant.set_device_preferences("spot-1", {"model_choice": "../bad"})["ok"] is False


def test_tool_subset_keeps_used_and_plugin_tools(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_tool_subset_size": 2})
    available = {"search_notes", "timer", "pc_stats", "custom_tool"}
    messages = [{"role": "tool", "name": "timer", "content": "{}"}]

    names = assistant.relevant_tool_names("hello", messages, available)

    assert "timer" in names
    assert "custom_tool" in names  # plugins have no keyword map but must remain callable
    assert len(names) <= 3


def test_validate_call_coerces_arguments_and_rejects_unknown_keys():
    fn = lambda text, enter=False: None  # noqa: E731 - signature fixture
    clean, error = assistant._validate_call(
        {"name": "type_text", "args": {"text": "hello", "enter": "yes"}}, {"type_text": fn})
    assert error is None
    assert clean["args"] == {"text": "hello", "enter": True}

    bad, error = assistant._validate_call(
        {"name": "type_text", "args": {"text": "hi", "bogus": 1}}, {"type_text": fn})
    assert bad is None and "bogus" in error


def test_dry_run_previews_state_changing_tools(monkeypatch):
    ran = []
    calls = []

    class Store:
        def remember(self, text, category="fact"):
            ran.append(text)
            return {"ok": True}

        def recent_turns(self, limit=6):
            return []

        def stats(self):
            return {}

    async def fake_model(self, messages, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"text": "", "calls": [{"name": "remember", "args": {"text": "B2"}}]}
        return {"text": "Previewed.", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({"remember": Store().remember}, store=Store())
    result = asyncio.run(engine.ask("remember B2", dry_run=True))

    assert result["op"] == "reply" and result["text"] == "Previewed."
    assert "remember" in result["trace"]
    assert ran == [], "dry-run must never execute a state-changing tool"


def test_undo_reverses_the_newest_reversible_action():
    forgotten = []

    class Store:
        def remember(self, text, category="fact"):
            return {"ok": True, "text": text}

        def forget(self, text):
            forgotten.append(text)
            return {"ok": True, "deleted": 1}

        def recent_turns(self, limit=6):
            return []

        def stats(self):
            return {}

    store = Store()
    engine = assistant.AssistantEngine({"remember": store.remember}, store=store)
    call = {"name": "remember", "args": {"text": "parking is B2"}}
    run = asyncio.run(engine._run_tool(call, "spot-1"))

    assert run["ok"] is True
    undone = engine.undo_last("spot-1")

    assert undone["ok"] is True and undone["reversed"] == "remember"
    assert forgotten == ["parking is B2"]
    assert engine.undo_last("spot-1")["ok"] is False


def test_plugin_tools_and_hooks_are_loaded(monkeypatch, tmp_path):
    folder = tmp_path / "plugins"
    folder.mkdir()
    marker = tmp_path / "reply.txt"
    (folder / "demo.py").write_text(
        "def register(api):\n"
        "    api.register_tool('ping', lambda: {'ok': True}, 'Ping', {})\n"
        "    api.on('reply', lambda event: open(%r, 'w', encoding='utf-8').write(event['text']))\n"
        % str(marker), encoding="utf-8",
    )
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_plugin_dir": str(folder)})

    engine = assistant.AssistantEngine({})

    assert "ping" in engine.tools and "ping" in engine.tool_specs
    schemas = assistant._tool_schemas([], "ping", set(engine.tools), engine.tool_specs)
    schema = next(s for s in schemas if s["name"] == "ping")
    assert schema["description"] == "Ping"
    asyncio.run(engine._emit(None, "reply", text="plugin saw this"))
    assert marker.read_text(encoding="utf-8") == "plugin saw this"


def test_home_context_is_appended_to_the_system_prompt_and_can_be_cleared():
    assistant.set_home_context("Kitchen light on; front door locked")
    try:
        assert "Current home state" in assistant.system_prompt()
        assert "front door locked" in assistant.system_prompt()
    finally:
        assistant.set_home_context("")
    assert "Current home state" not in assistant.system_prompt()


def test_pause_markers_and_sentence_splitting(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_pause_marker": "[pause]"})
    visible, pauses = assistant.pause_markers("Wait [pause] then go.")
    assert visible == "Wait then go." and pauses == 1

    sentences, offset = assistant.complete_sentences("Hello there. Next one!")
    assert sentences == ["Hello there.", "Next one!"]
    more, _ = assistant.complete_sentences("Hello there. Next one! And more.", offset)
    assert more == ["And more."]


def test_gemini_ids_never_come_from_the_local_vision_model(monkeypatch):
    seen = {}

    def fake_post(self, url, body, headers, timeout):
        seen["url"] = url
        if "model" in body:
            seen["model"] = body["model"]
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "message": {"content": "ok"}}

    monkeypatch.setattr(assistant.AssistantEngine, "_post", fake_post)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_vision_model": "qwen2.5vl:7b",
        "assistant_model_choice": "gemini:gemini-2.5-flash",
    })
    engine = assistant.AssistantEngine({})
    messages = [{"role": "user", "content": "look", "images": ["QUJD"]}]

    assert engine._gemini_model() == "gemini-2.5-flash"
    engine._local(messages)
    assert seen["model"] == "qwen2.5vl:7b"
    engine._gemini(messages)
    assert "gemini-2.5-flash" in seen["url"]


def test_vision_model_default_works_when_an_old_install_has_an_empty_key(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_vision_model": ""})
    assert assistant._vision_model() == "qwen2.5vl:7b"


def test_gemini_only_vision_falls_back_to_local_when_cloud_fails(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_route_vision": "gemini",
        "assistant_vision_model": "qwen2.5vl:7b",
        "assistant_model_choice": "local:qwen3:14b",
        "gemini_api_key": "test-key",
    })
    engine = assistant.AssistantEngine({})

    def bad_gemini(self, *args, **kwargs):
        raise RuntimeError("HTTP 400")

    def good_local(self, *args, **kwargs):
        return {"text": "local vision", "calls": [], "_model": "qwen2.5vl:7b"}

    monkeypatch.setattr(assistant.AssistantEngine, "_gemini", bad_gemini)
    monkeypatch.setattr(assistant.AssistantEngine, "_local", good_local)
    messages = [{"role": "user", "content": "look", "images": ["QUJD"]}]

    result = asyncio.run(engine._call_model(messages, token="token", device="spot-1"))

    assert result["text"] == "local vision"
    assert engine.metrics[-1]["backend"] == "local"
    assert engine.metrics[-1]["fallback"] is True


def test_look_at_screen_falls_back_to_local_vision_on_gemini_error(monkeypatch):
    class Shot:
        @staticmethod
        def capture():
            return {"ok": True, "image": "QUJD", "window_title": "Notepad"}

    monkeypatch.setitem(sys.modules, "screenshot", Shot)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_vision_backend": "gemini",
        "assistant_vision_model": "qwen2.5vl:7b",
        "assistant_gemini_model": "gemini-2.5-flash",
        "gemini_api_key": "test-key",
    })
    seen = []

    def fake_post(url, body, headers, timeout):
        seen.append(url)
        if "googleapis" in url:
            raise RuntimeError("HTTP 400")
        return {"message": {"content": "local answer"}}

    monkeypatch.setattr(assistant, "_post_json", fake_post)
    result = assistant.look_at_screen("what is that?")

    assert result["ok"] is True and result["answer"] == "local answer"
    assert result["fallback"] == "local"
    assert any("11434" in url for url in seen)


def test_look_at_screen_uses_a_gemini_model_id_not_the_local_vision_tag(monkeypatch):
    seen = {}

    class Shot:
        @staticmethod
        def capture():
            return {"ok": True, "image": "QUJD", "window_title": "Notepad"}

    monkeypatch.setitem(sys.modules, "screenshot", Shot)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "assistant_vision_backend": "gemini",
        "assistant_vision_model": "qwen2.5vl:7b",
        "assistant_gemini_model": "gemini-2.5-flash",
        "gemini_api_key": "test-key",
    })

    def fake_post(url, body, headers, timeout):
        seen["url"] = url
        return {"candidates": [{"content": {"parts": [{"text": "a cat"}]}}]}

    monkeypatch.setattr(assistant, "_post_json", fake_post)
    result = assistant.look_at_screen("what is that?")

    assert result["ok"] is True and result["answer"] == "a cat"
    assert "gemini-2.5-flash" in seen["url"]
    assert "qwen2.5vl" not in seen["url"]


def test_stream_emits_an_unpunctuated_final_clause(monkeypatch):
    class Response:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]

        def __iter__(self):
            return iter(self.lines)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    lines = [json.dumps({"message": {"content": piece}})
             for piece in ("Hello there.", " and one last thing")]
    monkeypatch.setattr(assistant.urllib.request, "urlopen",
                        lambda req, timeout: Response(lines))
    seen = []
    monkeypatch.setattr(assistant.AssistantEngine, "_emit_sync",
                        lambda self, emit, op, **data: seen.append((op, data)))

    engine = assistant.AssistantEngine({})
    result = engine._post_stream("http://ollama/api/chat", {}, {}, 1.0,
                                 token="token", emit=lambda event: None, device="spot-1")

    assert result["text"] == "Hello there. and one last thing"
    assert [op for op, _ in seen].count("speak") == 2
    assert seen[-1][1]["text"] == "and one last thing"


def test_image_tool_result_is_attached_to_the_tool_message(monkeypatch):
    calls = []
    messages_seen = []

    async def fake_model(self, messages, *args, **kwargs):
        messages_seen.append([dict(m) for m in messages])
        if not calls:
            calls.append(1)
            return {"text": "", "calls": [{"name": "look_at_screen", "args": {"question": "what is that?"}}]}
        return {"text": "It is a cat.", "calls": []}

    monkeypatch.setattr(assistant.AssistantEngine, "_call_model", fake_model)
    engine = assistant.AssistantEngine({
        "look_at_screen": lambda question: {"ok": True, "answer": "a cat", "image": "QUJD"},
    })

    pending = asyncio.run(engine.ask("what is on the screen", device="spot-1"))
    assert pending["op"] == "confirm"
    result = asyncio.run(engine.confirm(pending["token"], True))

    tool_msg = next(m for m in messages_seen[1] if m["role"] == "tool")
    assert tool_msg["images"] == ["QUJD"]
    assert "image" not in json.loads(tool_msg["content"])
    assert result["device"] == "spot-1"
