"""The Claude face's state machine: hook events in, one status out."""
import claudemon


def mon():
    async def broadcast(_data):
        pass

    async def notify(*_a, **_k):
        pass

    return claudemon.ClaudeMon(broadcast, notify)


def test_a_turn_walks_idle_thinking_working_done():
    m = mon()
    assert m.status == "offline"

    m.on_event({"event": "SessionStart", "session": "abcd1234", "project": "EchoPortal"})
    assert m.status == "idle"
    assert m.project == "EchoPortal"

    m.on_event({"event": "UserPromptSubmit", "prompt": "add a dark mode toggle"})
    assert m.status == "thinking"
    assert m.prompt == "add a dark mode toggle"

    m.on_event({"event": "PreToolUse", "tool": "Edit", "detail": "SettingsPage.tsx"})
    assert m.status == "working"
    assert (m.tool, m.detail) == ("Edit", "SettingsPage.tsx")

    m.on_event({"event": "PostToolUse", "tool": "Edit", "ok": True})
    assert m.status == "thinking"

    m.on_event({"event": "Stop"})
    assert m.status == "done"
    assert m.tool == ""


def test_tool_counters_count_the_turn_and_the_session():
    m = mon()
    m.on_event({"event": "UserPromptSubmit", "prompt": "first"})
    for _ in range(3):
        m.on_event({"event": "PreToolUse", "tool": "Bash", "detail": "ls"})
    assert m.tools_turn == 3
    assert m.tools_session == 3

    m.on_event({"event": "UserPromptSubmit", "prompt": "second"})
    assert m.tools_turn == 0, "a new request restarts the per-turn count"
    m.on_event({"event": "PreToolUse", "tool": "Bash", "detail": "ls"})
    assert m.tools_turn == 1
    assert m.tools_session == 4


def test_a_notification_asks_for_the_user_and_raises_a_chiming_banner():
    m = mon()
    banner = m.on_event({"event": "Notification", "message": "Claude needs your permission to use Bash"})
    assert m.status == "waiting"
    assert banner is not None
    title, text, chime = banner
    assert title == "Claude needs you"
    assert "permission" in text
    assert chime is True


def test_finishing_raises_a_quiet_banner():
    m = mon()
    m.on_event({"event": "UserPromptSubmit", "prompt": "run the tests"})
    banner = m.on_event({"event": "Stop"})
    assert banner is not None
    assert banner[0] == "Claude finished"
    assert banner[2] is False, "finishing should not chime"


def test_a_new_session_resets_the_session_counters():
    m = mon()
    m.on_event({"event": "SessionStart", "session": "aaaa1111"})
    m.on_event({"event": "PreToolUse", "tool": "Read", "detail": "x.py"})
    assert m.tools_session == 1
    m.on_event({"event": "SessionStart", "session": "bbbb2222"})
    assert m.tools_session == 0
    assert m.session == "bbbb2222"


def test_subagents_are_counted_and_never_negative():
    m = mon()
    m.on_event({"event": "SubagentStart"})
    m.on_event({"event": "SubagentStart"})
    assert m.subagents == 2
    m.on_event({"event": "SubagentStop"})
    assert m.subagents == 1
    m.on_event({"event": "SubagentStop"})
    m.on_event({"event": "SubagentStop"})
    assert m.subagents == 0


def test_the_activity_log_is_bounded_and_the_snapshot_is_short():
    m = mon()
    for i in range(40):
        m.on_event({"event": "PreToolUse", "tool": "Bash", "detail": "step %d" % i})
    assert len(m.events) <= 30
    snap = m.snapshot()
    assert len(snap["events"]) <= 10
    assert snap["events"][-1].endswith("step 39")


def test_the_snapshot_carries_what_the_face_draws():
    m = mon()
    m.on_event({"event": "UserPromptSubmit", "prompt": "hello", "model": "claude-opus-5"})
    snap = m.snapshot()
    for key in ("up", "status", "tool", "detail", "prompt", "project", "model",
                "turn_elapsed", "tools_turn", "tools_session", "subagents", "events"):
        assert key in snap
    assert snap["status"] == "thinking"
    assert snap["model"] == "claude-opus-5"
    assert snap["up"] is True


def test_an_unknown_event_is_ignored_without_changing_the_status():
    m = mon()
    m.on_event({"event": "SessionStart"})
    m.on_event({"event": "SomethingNewInAFutureVersion"})
    assert m.status == "idle"
