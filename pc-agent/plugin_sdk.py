"""Tiny extension API for user-installed Jarvis plugins.

A plugin file defines ``register(api)``. It may subscribe to assistant events with
``api.on(name, fn)`` and add a callable tool with ``api.register_tool(name, fn, ...)``. Plugins are
optional user code: a failing plugin is ignored so the assistant still starts.
"""
import asyncio
import inspect


class PluginAPI:
    """The small surface exposed to plugin files; no hub or config internals are handed out."""

    def __init__(self, engine):
        self._engine = engine

    def on(self, name: str, fn) -> None:
        """Subscribe to prompt, thinking, tool, stream, confirm, reply, error or cancelled."""
        if callable(fn) and str(name or "").strip():
            hooks = self._engine._plugin_hooks
            hooks.setdefault(str(name).strip().lower(), []).append(fn)

    def register_tool(self, name: str, fn, description: str = "", parameters: dict | None = None):
        """Add a read-only tool. State-changing actions belong in agent.py's confirm allowlist."""
        from assistant import TOOL_BY_NAME  # noqa: PLC0415 - avoids an import cycle at startup

        key = str(name or "").strip()
        if not key or not callable(fn) or key in TOOL_BY_NAME or key in self._engine.tools:
            return False
        types = {"string", "boolean", "integer", "number"}
        specs = {}
        for param, kind in (parameters or {}).items():
            pname = str(param or "").strip()
            if not pname or not pname.isidentifier():
                return False
            specs[pname] = str(kind).strip().lower() if str(kind).strip().lower() in types else "string"
        self._engine.tool_specs[key] = {
            "name": key,
            "description": str(description or "User plugin tool.").strip()[:300],
            "parameters": specs,
            "confirm": False,
        }
        self._engine.tools[key] = fn
        return True

    def emit(self, name: str, **data) -> None:
        """Send an optional progress event to connected clients."""
        target = self._engine.broadcast
        if target is None:
            return
        result = target({"op": str(name or "plugin"), **data})
        if inspect.isawaitable(result):
            loop = getattr(self._engine, "_async_loop", None)
            if loop is not None and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(result, loop)
            else:
                result.close()
