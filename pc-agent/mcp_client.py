"""Optional Model Context Protocol (MCP) tool provider for the Jarvis brain.

Configure servers in config.json, for example::

    "mcp_servers": {
      "files": {"url": "http://127.0.0.1:8000/mcp", "token": "", "tools": []}
    }

Each server is discovered with the MCP Streamable HTTP JSON-RPC handshake. Discovered tools are
registered through ``PluginAPI.register_tool`` so they get the same schemas, validation, metrics
and tool loop as built-in Jarvis tools. An unreachable server is logged and skipped; it never
prevents the agent from starting.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import urllib.error
import urllib.request

import config

PROTOCOL_VERSION = "2025-06-18"
_IDENTIFIER = re.compile(r"\W+")


def _identifier(value: str) -> str:
    """MCP names may contain dots or dashes; Python tools must be identifiers."""
    clean = _IDENTIFIER.sub("_", str(value or "")).strip("_")
    return clean or "tool"


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_body(raw: bytes, content_type: str) -> dict:
    """Accept a plain JSON body or an SSE stream of JSON-RPC responses."""
    text = raw.decode("utf-8", "ignore").strip()
    if not text:
        return {}
    if "text/event-stream" not in content_type.lower():
        try:
            body = json.loads(text)
            return body if isinstance(body, dict) else {}
        except ValueError:
            return {}
    result = {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            body = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(body, dict) and ("result" in body or "error" in body):
            result = body
    return result


def rpc(url: str, token: str, timeout: float, method: str, params: dict | None = None,
        session_id: str = "", notify: bool = False) -> tuple[dict, str]:
    """One JSON-RPC POST. Returns (result, session_id)."""
    payload = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if not notify:
        payload["id"] = 1
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "EchoPortal Jarvis MCP client",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            content_type = str(response.headers.get("Content-Type", "") or "")
            session = str(response.headers.get("Mcp-Session-Id", "") or "") or session_id
    except urllib.error.HTTPError as exc:
        raise RuntimeError("MCP HTTP %s" % exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("MCP request failed: %s" % exc) from exc
    if notify:
        return {}, session
    body = parse_body(raw, content_type)
    if "error" in body:
        raise RuntimeError(str(body["error"])[:200])
    return body.get("result", {}) if isinstance(body.get("result"), dict) else {}, session


def discover(url: str, token: str, timeout: float) -> tuple[list[dict], str]:
    """Initialize the session and page through tools/list; returns (tools, session_id)."""
    result, session = rpc(url, token, timeout, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "EchoPortal Jarvis", "version": "1.0"},
    })
    if result:
        rpc(url, token, timeout, "notifications/initialized", session_id=session, notify=True)
    tools: list[dict] = []
    cursor = ""
    for _ in range(20):  # bound a misbehaving server's pagination
        params = {"cursor": cursor} if cursor else {}
        result, session = rpc(url, token, timeout, "tools/list", params, session)
        page = result.get("tools")
        if isinstance(page, list):
            tools.extend(t for t in page if isinstance(t, dict))
        cursor = str(result.get("nextCursor") or "")
        if not cursor:
            break
    return tools, session


def make_wrapper(ctx: dict, parameters: dict):
    """Build a tool callable whose explicit signature survives Jarvis validation."""
    async def call(**kwargs):
        args = {ctx["mapping"][key]: value for key, value in kwargs.items()
                if key in ctx["mapping"] and value is not None}
        # rpc() is blocking urllib; keep an unreachable server from stalling the agent loop.
        result, _session = await asyncio.to_thread(
            rpc, ctx["url"], ctx["token"], ctx["timeout"], "tools/call",
            {"name": ctx["tool"], "arguments": args}, ctx["session"],
        )
        content = result.get("content") or []
        texts = [str(item.get("text") or "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        if result.get("isError"):
            return {"ok": False, "error": (" ".join(texts) or "MCP tool failed")[:1000]}
        structured = result.get("structuredContent")
        payload = structured if isinstance(structured, dict) else (" ".join(texts) or result)
        return {"ok": True, "result": _json_safe(payload)}

    params = []
    for name, kind in parameters.items():
        params.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, default=None,
            annotation=bool if kind == "boolean" else str,
        ))
    call.__signature__ = inspect.Signature(params)
    call.__name__ = "mcp_" + _identifier(ctx["tool"])
    call.__doc__ = "Call the MCP tool " + ctx["tool"]
    return call


def _server_settings(value) -> dict:
    if isinstance(value, str):
        return {"url": value}
    return value if isinstance(value, dict) else {}


def register_mcp_tools(api) -> None:
    """Register every configured MCP server's tools; failures are non-fatal."""
    servers = config.get("mcp_servers", {}) or {}
    if isinstance(servers, (list, tuple)):
        servers = {"server%d" % i: item for i, item in enumerate(servers)}
    if not isinstance(servers, dict):
        return
    timeout = max(1.0, min(60.0, float(config.get("mcp_timeout", 10) or 10)))
    for label, raw in servers.items():
        settings = _server_settings(raw)
        url = str(settings.get("url", "") or "").strip()
        if not url:
            continue
        token = str(settings.get("token", "") or "").strip()
        allow = [str(x).strip() for x in (settings.get("tools") or [])] if isinstance(
            settings.get("tools"), (list, tuple)) else []
        prefix = "mcp_" + _identifier(label) + "_"
        try:
            tools, session = discover(url, token, timeout)
        except Exception as exc:  # noqa: BLE001 - one bad server must not stop the agent
            print("MCP server %s skipped: %s" % (label, exc))
            continue
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if not name or (allow and name not in allow and prefix + _identifier(name) not in allow):
                continue
            tool_name = prefix + _identifier(name)
            properties = ((tool.get("inputSchema") or {}).get("properties") or {})
            parameters: dict[str, str] = {}
            mapping: dict[str, str] = {}
            if isinstance(properties, dict):
                for original, meta in properties.items():
                    clean = _identifier(original)
                    kind = "boolean" if isinstance(meta, dict) and meta.get("type") == "boolean" else "string"
                    parameters[clean] = kind
                    mapping[clean] = str(original)
            ctx = {"url": url, "token": token, "timeout": timeout, "session": session,
                   "tool": name, "mapping": mapping}
            try:
                api.register_tool(tool_name, make_wrapper(ctx, parameters),
                                  str(tool.get("description") or "MCP tool " + name)[:300], parameters)
            except Exception as exc:  # noqa: BLE001
                print("MCP tool %s skipped: %s" % (tool_name, exc))
