"""MCP servers are optional: registration is schema-safe and network failures are non-fatal."""
import asyncio
import inspect

import config
import mcp_client


class FakeAPI:
    def __init__(self):
        self.registered = []

    def register_tool(self, name, fn, description="", parameters=None):
        self.registered.append((name, fn, description, parameters or {}))
        return True


def test_parse_body_accepts_plain_json_and_sse():
    assert mcp_client.parse_body(b'{"result":{"ok":1}}', "application/json") == {"result": {"ok": 1}}
    stream = (b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}\n\n"
              b"data: not json\n\n")
    assert mcp_client.parse_body(stream, "text/event-stream")["result"] == {"tools": []}


def test_register_mcp_tools_creates_validator_safe_wrappers(monkeypatch):
    monkeypatch.setitem(config.VALUES, "mcp_servers", {
        "files": {"url": "http://127.0.0.1:9000/mcp", "tools": ["search"]},
    })
    tools = [{"name": "search", "description": "Search files",
              "inputSchema": {"properties": {"search-query": {"type": "string"},
                                             "loud": {"type": "boolean"}}}},
             {"name": "skipped", "inputSchema": {"properties": {}}}]
    monkeypatch.setattr(mcp_client, "discover", lambda *a: (tools, "session-7"))
    calls = []

    def fake_rpc(url, token, timeout, method, params=None, session_id="", notify=False):
        calls.append((method, params, session_id))
        return {"content": [{"type": "text", "text": "found it"}]}, session_id

    monkeypatch.setattr(mcp_client, "rpc", fake_rpc)
    api = FakeAPI()
    mcp_client.register_mcp_tools(api)

    assert len(api.registered) == 1
    name, fn, description, parameters = api.registered[0]
    assert name == "mcp_files_search" and description == "Search files"
    assert parameters == {"search_query": "string", "loud": "boolean"}
    signature = inspect.signature(fn)
    assert set(signature.parameters) == {"search_query", "loud"}
    assert all(p.default is None for p in signature.parameters.values())
    assert asyncio.run(fn(search_query="jarvis", loud="true")) == {"ok": True, "result": "found it"}
    method, params, session = calls[0]
    assert method == "tools/call" and session == "session-7"
    assert params == {"name": "search", "arguments": {"search-query": "jarvis", "loud": "true"}}


def test_register_mcp_tools_skips_unreachable_servers(monkeypatch, capsys):
    monkeypatch.setitem(config.VALUES, "mcp_servers", {"down": {"url": "http://127.0.0.1:1/mcp"}})

    def fail(*args):
        raise RuntimeError("offline")

    monkeypatch.setattr(mcp_client, "discover", fail)
    api = FakeAPI()
    mcp_client.register_mcp_tools(api)
    assert api.registered == []
    assert "MCP server down skipped" in capsys.readouterr().out
