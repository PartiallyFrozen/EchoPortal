"""Search providers and page reading: urlopen is always stubbed, no real network."""
import json

import pytest

import config
import search_providers

DDG_PAGE = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fecho&amp;rut=abc">Echo Portal</a>
<div class="result__snippet">A local &amp; private dashboard</div>
</body></html>
"""


class _Response:
    def __init__(self, payload: bytes, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self.payload if amount < 0 else self.payload[:amount]


def _values(**overrides):
    return {**config.VALUES, "search_provider": "auto", "search_searxng_url": "", "search_allow_private": False, **overrides}


def test_auto_prefers_a_configured_provider_then_falls_back_to_ddg(monkeypatch):
    for key in ("BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "VALUES", _values())

    assert search_providers._provider() == "duckduckgo"
    monkeypatch.setenv("SERPER_API_KEY", "sk")
    assert search_providers._provider() == "serper"
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    assert search_providers._provider() == "brave"
    monkeypatch.setattr(config, "VALUES", _values(search_searxng_url="https://searx.example/"))
    assert search_providers._provider() == "searxng"
    monkeypatch.setattr(config, "VALUES", _values(search_provider="tavily"))
    assert search_providers._provider() == "tavily"


def test_duckduckgo_html_fallback_is_parsed_without_an_api_key(monkeypatch):
    monkeypatch.setattr(config, "VALUES", _values())
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Response(DDG_PAGE.encode("utf-8"))

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)
    hits = search_providers.search("echo portal", 3)

    assert hits == [{"title": "Echo Portal", "url": "https://example.com/echo", "snippet": "A local & private dashboard"}]
    assert "q=echo+portal" in seen["url"]


def test_brave_calls_the_json_api_with_its_subscription_key(monkeypatch):
    monkeypatch.setattr(config, "VALUES", _values(search_provider="brave"))
    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    seen = {}
    payload = {"web": {"results": [{"title": "Result", "url": "https://example.com", "description": "A snippet"}]}}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)
    hits = search_providers.search("hello", 2)

    assert hits == [{"title": "Result", "url": "https://example.com", "snippet": "A snippet"}]
    assert "count=2" in seen["url"]
    assert seen["headers"]["x-subscription-token"] == "brave-key"


@pytest.mark.parametrize("provider,host", [
    ("searxng", "searx.example"),
    ("serper", "google.serper.dev"),
    ("tavily", "api.tavily.com"),
])
def test_remaining_json_providers_return_normalized_hits(monkeypatch, provider, host):
    monkeypatch.setenv("SERPER_API_KEY", "serper-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setattr(config, "VALUES", _values(search_provider=provider, search_searxng_url="https://searx.example"))
    payloads = {
        "searxng": {"results": [{"title": "S", "url": "https://s.example", "content": "snippet"}]},
        "serper": {"organic": [{"title": "G", "url": "https://g.example", "snippet": "snippet"}]},
        "tavily": {"results": [{"title": "T", "url": "https://t.example", "content": "snippet"}]},
    }
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = req.data
        return _Response(json.dumps(payloads[provider]).encode("utf-8"))

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)
    hits = search_providers.search("query")

    assert host in seen["url"]
    assert len(hits) == 1
    assert hits[0]["snippet"] == "snippet"
    if provider == "searxng":
        assert "format=json" in seen["url"]
    if provider == "tavily":
        assert json.loads(seen["body"].decode("utf-8"))["api_key"] == "tavily-key"


def test_a_failing_api_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setattr(config, "VALUES", _values(search_provider="brave"))

    def boom(query, limit):
        raise OSError("provider down")

    monkeypatch.setitem(search_providers._SEARCH, "brave", boom)
    monkeypatch.setattr(search_providers, "_duckduckgo", lambda q, n: [{"title": "fallback", "url": "https://example.com", "snippet": ""}])
    assert search_providers.search("query")[0]["title"] == "fallback"


def test_fetch_url_rejects_non_http_and_private_hosts_without_network(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Response(b"ok")

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)
    for url in ("file:///etc/passwd", "http://127.0.0.1:8123", "http://localhost/x", "http://192.168.1.4", "http://[::1]/", "http://169.254.169.254/"):
        assert search_providers.fetch_url(url)["ok"] is False
    assert calls == [], "blocked URLs must never reach urlopen"

    monkeypatch.setattr(config, "VALUES", _values(search_allow_private=True))
    assert search_providers.fetch_url("http://127.0.0.1:8123")["ok"] is True
    assert calls == ["http://127.0.0.1:8123"]


def test_fetch_url_extracts_a_title_and_plain_text(monkeypatch):
    monkeypatch.setattr(config, "VALUES", _values())
    page = b"<html><head><title>Hello &amp; welcome</title><script>bad()</script></head><body><p>Good text here</p></body></html>"

    def fake_urlopen(req, timeout=None):
        return _Response(page, {"Content-Type": "text/html; charset=utf-8"})

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)
    result = search_providers.fetch_url("https://example.com/page")

    assert result["ok"] is True
    assert result["title"] == "Hello & welcome"
    assert "Good text here" in result["text"]
    assert "bad()" not in result["text"]

    short = search_providers.fetch_url("https://example.com/page", limit_chars=20)
    assert short["ok"] is True
    assert len(short["text"]) <= 20
