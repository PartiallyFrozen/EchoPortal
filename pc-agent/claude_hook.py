"""Claude Code hook -> EchoPortal agent, over one UDP datagram (fast: stdlib only, never blocks Claude Code).

Configured in ~/.claude/settings.json for SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop, SubagentStop, SessionEnd. Reads the hook JSON on stdin, sends a compact event to
127.0.0.1:8767 where claudemon.py (inside the agent) turns it into the Spot's Claude face.
"""
import json
import os
import socket
import sys

PORT = 8767


def detail_for(tool, inp):
    """One short line describing what a tool call is doing (bounded: the face has one line to draw it)."""
    return _detail_for(tool, inp)[:80]


def _detail_for(tool, inp):
    if not isinstance(inp, dict):
        return ""
    if tool in ("Bash", "PowerShell"):
        return inp.get("description") or (inp.get("command") or "")[:70]
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        return os.path.basename(inp.get("file_path") or inp.get("notebook_path") or "")
    if tool in ("Grep", "Glob"):
        return inp.get("pattern", "")
    if tool in ("Agent", "Task"):
        return inp.get("description", "")
    if tool in ("WebFetch", "WebSearch"):
        return inp.get("url") or inp.get("query") or ""
    if tool == "Skill":
        return inp.get("skill", "")
    if tool == "SendUserFile":
        return ", ".join(os.path.basename(p) for p in inp.get("files", []))[:60]
    for k in ("description", "title", "query", "prompt"):
        if isinstance(inp.get(k), str):
            return inp[k][:70]
    return ""


def main():
    try:
        h = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return
    ev = h.get("hook_event_name", "")
    tool = h.get("tool_name", "") or ""
    out = {
        "event": ev,
        "session": (h.get("session_id") or "")[:8],
        "project": os.path.basename((h.get("cwd") or "").rstrip("\\/")),
        "tool": tool,
        "detail": detail_for(tool, h.get("tool_input")),
        "prompt": (h.get("prompt") or "")[:160],
        "message": (h.get("message") or h.get("title") or "")[:120],
        "model": h.get("model") or "",
        "mode": h.get("permission_mode") or "",
    }
    if ev == "PostToolUse":
        r = h.get("tool_response")
        out["ok"] = not (isinstance(r, dict) and (r.get("error") or r.get("is_error")))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(json.dumps(out).encode("utf-8"), ("127.0.0.1", PORT))
        s.close()
    except OSError:
        pass


if __name__ == "__main__":
    main()
