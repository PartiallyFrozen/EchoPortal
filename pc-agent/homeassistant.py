"""Home Assistant REST adapter for the assistant's smart-home tools.

Configuration lives in config.json: ``home_assistant_url``, ``home_assistant_token`` and the
``home_assistant_services`` allowlist. A service that is not on that list is refused before any
request is made, and an empty URL/token means the adapter reports itself as unconfigured.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import config

TIMEOUT = 8.0


def _safe(value):
    """Keep responses JSON-safe even if a future client hands us tuples or custom objects."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class HomeAssistantClient:
    """Small REST client; safe to construct even with no configuration."""

    def configured(self) -> bool:
        url = str(config.get("home_assistant_url", "") or "").strip()
        token = str(config.get("home_assistant_token", "") or "").strip()
        return bool(url and token)

    def _request(self, method: str, path: str, body=None) -> dict:
        base = str(config.get("home_assistant_url", "") or "").strip().rstrip("/")
        if not base:
            return {"ok": False, "error": "home_assistant_url is not configured"}
        token = str(config.get("home_assistant_token", "") or "").strip()
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "EchoPortal Jarvis",
        }
        data = None
        if body is not None:
            data = json.dumps(_safe(body)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = base + (path if path.startswith("/") else "/" + path)
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read(1_000_000)
                status = int(getattr(resp, "status", 200) or 200)
            payload = json.loads(raw.decode("utf-8", "ignore")) if raw else {}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": int(e.code), "error": "Home Assistant HTTP %s: %s" % (e.code, e.reason)}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return {"ok": False, "error": "Home Assistant request failed: %s" % e}
        return {"ok": True, "status": status, "data": _safe(payload)}

    def get_states(self, entity_id: str | None = None) -> dict:
        if not self.configured():
            return {"ok": False, "error": "Home Assistant is not configured"}
        path = "/api/states"
        if entity_id:
            path += "/" + urllib.parse.quote(str(entity_id), safe="")
        result = self._request("GET", path)
        if isinstance(result, list):
            return {"ok": True, "states": [_safe(s) for s in result], "count": len(result)}
        if not isinstance(result, dict):
            return {"ok": False, "error": "unexpected response from Home Assistant"}
        if not result.get("ok", True):
            return result
        data = result["data"] if "data" in result else result.get("states", result.get("state", {}))
        if isinstance(data, list):
            return {"ok": True, "states": [_safe(s) for s in data], "count": len(data)}
        return {"ok": True, "state": _safe(data)}

    def call_service(self, domain: str, service: str, entity_id: str | None = None, data: dict | None = None) -> dict:
        domain, service = str(domain).strip().lower(), str(service).strip().lower()
        name = "%s.%s" % (domain, service)
        allowed = config.get("home_assistant_services", []) or []
        allowed = [str(x).strip().lower() for x in allowed] if isinstance(allowed, (list, tuple, set)) else []
        if name not in allowed:
            return {"ok": False, "error": "%s is not in home_assistant_services" % name}
        if not self.configured():
            return {"ok": False, "error": "Home Assistant is not configured"}
        body = dict(data) if isinstance(data, dict) else {}
        if entity_id:
            body["entity_id"] = str(entity_id)
        path = "/api/services/%s/%s" % (urllib.parse.quote(domain, safe=""), urllib.parse.quote(service, safe=""))
        result = self._request("POST", path, body)
        if not isinstance(result, dict) or not result.get("ok", True):
            return result if isinstance(result, dict) else {"ok": False, "error": "unexpected response from Home Assistant"}
        return {
            "ok": True,
            "service": name,
            "entity_id": str(entity_id) if entity_id else None,
            "data": _safe(result.get("data", {})),
        }
