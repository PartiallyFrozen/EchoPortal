"""Home Assistant REST adapter: all requests are stubbed, only config is changed."""
import json
import urllib.error

import config
import homeassistant

SERVICES = ["light.turn_on", "light.turn_off"]


def _configure(monkeypatch, url="http://ha.local:8123/", token="secret", services=None):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES,
        "home_assistant_url": url,
        "home_assistant_token": token,
        "home_assistant_services": SERVICES if services is None else services,
    })


class _Response:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self.payload if amount < 0 else self.payload[:amount]


def test_configured_requires_both_url_and_token(monkeypatch):
    _configure(monkeypatch, token="")
    assert homeassistant.HomeAssistantClient().configured() is False
    _configure(monkeypatch, url="")
    assert homeassistant.HomeAssistantClient().configured() is False
    _configure(monkeypatch)
    assert homeassistant.HomeAssistantClient().configured() is True


def test_get_states_reads_all_or_one_entity(monkeypatch):
    _configure(monkeypatch)
    seen = []

    def fake_request(self, method, path, body=None):
        seen.append((method, path, body))
        return {"ok": True, "data": [{"entity_id": "light.kitchen", "state": "on"}]}

    monkeypatch.setattr(homeassistant.HomeAssistantClient, "_request", fake_request)
    client = homeassistant.HomeAssistantClient()

    all_states = client.get_states()
    assert all_states["count"] == 1
    assert all_states["states"][0]["entity_id"] == "light.kitchen"
    client.get_states("light.kitchen")
    assert seen == [("GET", "/api/states", None), ("GET", "/api/states/light.kitchen", None)]


def test_get_states_is_unconfigured_without_credentials(monkeypatch):
    _configure(monkeypatch, token="")
    assert homeassistant.HomeAssistantClient().get_states()["ok"] is False


def test_call_service_is_fail_closed_and_forwards_allowed_calls(monkeypatch):
    _configure(monkeypatch)
    seen = []

    def fake_request(self, method, path, body=None):
        seen.append((method, path, body))
        return {"ok": True, "data": []}

    monkeypatch.setattr(homeassistant.HomeAssistantClient, "_request", fake_request)
    client = homeassistant.HomeAssistantClient()

    denied = client.call_service("lock", "lock", "lock.front")
    assert denied["ok"] is False
    assert "home_assistant_services" in denied["error"]
    assert seen == []

    result = client.call_service("light", "turn_on", "light.kitchen", {"brightness": 100})
    assert result["ok"] is True
    assert result["service"] == "light.turn_on"
    assert seen == [("POST", "/api/services/light/turn_on", {"brightness": 100, "entity_id": "light.kitchen"})]


def test_request_sends_bearer_token_and_parses_json(monkeypatch):
    _configure(monkeypatch)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["data"] = req.data
        return _Response(b'[{"entity_id":"light.kitchen"}]')

    monkeypatch.setattr(homeassistant.urllib.request, "urlopen", fake_urlopen)
    result = homeassistant.HomeAssistantClient().get_states()

    assert seen["url"] == "http://ha.local:8123/api/states"
    assert seen["method"] == "GET"
    assert seen["timeout"] == homeassistant.TIMEOUT
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert result["states"] == [{"entity_id": "light.kitchen"}]


def test_request_sends_a_json_body_and_reports_http_errors(monkeypatch):
    _configure(monkeypatch)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        if req.get_method() == "POST":
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        return _Response(b"{}")

    monkeypatch.setattr(homeassistant.urllib.request, "urlopen", fake_urlopen)
    client = homeassistant.HomeAssistantClient()

    failed = client.call_service("light", "turn_on", "light.kitchen")
    assert failed["ok"] is False
    assert "401" in failed["error"]
    assert seen["body"] == {"entity_id": "light.kitchen"}
