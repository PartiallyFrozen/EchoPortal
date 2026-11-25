"""Web search and page reading for the assistant, using only the standard library.

``search_provider`` selects duckduckgo, searxng, brave, serper or tavily. ``auto`` prefers a
configured JSON provider (SearXNG, Brave, Serper, Tavily in that order) and otherwise uses the
keyless DuckDuckGo HTML endpoint, copied here from assistant.py so search works on a fresh install.
"""
import html
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import config

USER_AGENT = "Mozilla/5.0 (EchoPortal Jarvis)"
TIMEOUT = 10.0
PROVIDERS = ("duckduckgo", "searxng", "brave", "serper", "tavily")

TAG_RE = re.compile(r"<[^>]+>")
DDG_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DDG_SNIP_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</div>', re.S)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.S | re.I)
LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "metadata.google.internal"}


def _strip_tags(s: str) -> str:
    return " ".join(html.unescape(TAG_RE.sub(" ", s)).split())


def _html_text(page: str) -> str:
    return " ".join(html.unescape(TAG_RE.sub(" ", SCRIPT_RE.sub(" ", page))).split())


def _open(req, timeout: float = TIMEOUT):
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with _open(req) as resp:
        return json.loads(resp.read(1_000_000).decode("utf-8", "ignore"))


def _post_json(url: str, body: dict, headers: dict | None = None) -> dict:
    hdrs = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=hdrs, method="POST")
    with _open(req) as resp:
        return json.loads(resp.read(1_000_000).decode("utf-8", "ignore"))


def _credential(env_name: str, config_name: str = "") -> str:
    value = os.environ.get(env_name) or (config.get(config_name, "") if config_name else "")
    return str(value or "").strip()


def _searxng_url() -> str:
    return str(config.get("search_searxng_url", "") or "").strip().rstrip("/")


def _provider() -> str:
    choice = str(config.get("search_provider", "auto") or "auto").strip().lower()
    if choice == "auto":
        if _searxng_url():
            return "searxng"
        for name, env_name in (("brave", "BRAVE_API_KEY"), ("serper", "SERPER_API_KEY"), ("tavily", "TAVILY_API_KEY")):
            if _credential(env_name):
                return name
        return "duckduckgo"
    return choice if choice in PROVIDERS else "duckduckgo"


def _item(row) -> dict | None:
    if not isinstance(row, dict):
        return None
    title = str(row.get("title") or row.get("name") or "")
    url = str(row.get("url") or row.get("link") or row.get("href") or "").strip()
    snippet = str(row.get("snippet") or row.get("description") or row.get("content") or "")
    if not url:
        return None
    return {"title": _strip_tags(title)[:200], "url": url, "snippet": _strip_tags(snippet)[:300]}


def _items(rows, limit: int) -> list[dict]:
    results = []
    for row in rows if isinstance(rows, list) else []:
        item = _item(row)
        if item:
            results.append(item)
            if len(results) >= limit:
                break
    return results


def _first_list(payload, *paths: str) -> list:
    for path in paths:
        node = payload
        for key in path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list):
            return node
    return []


def _duckduckgo(query: str, limit: int) -> list[dict]:
    """DuckDuckGo HTML results; no API key. Returns [] on failure."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _open(req) as resp:
            page = resp.read(600_000).decode("utf-8", "ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    results = []
    for (href, title), snippet in zip(DDG_LINK_RE.findall(page), DDG_SNIP_RE.findall(page)):
        href = html.unescape(href)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        item = _item({"title": title, "url": qs.get("uddg", [href])[0], "snippet": snippet})
        if item:
            results.append(item)
            if len(results) >= limit:
                break
    return results


def _searxng(query: str, limit: int) -> list[dict]:
    base = _searxng_url()
    if not base:
        return []
    url = base + "/search?" + urllib.parse.urlencode({"q": query, "format": "json", "language": "en"})
    return _items(_first_list(_get_json(url), "results"), limit)


def _brave(query: str, limit: int) -> list[dict]:
    key = _credential("BRAVE_API_KEY", "brave_api_key")
    if not key:
        return []
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": limit})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "X-Subscription-Token": key}
    return _items(_first_list(_get_json(url, headers), "web.results", "results"), limit)


def _serper(query: str, limit: int) -> list[dict]:
    key = _credential("SERPER_API_KEY", "serper_api_key")
    if not key:
        return []
    payload = _post_json("https://google.serper.dev/search", {"q": query, "num": limit}, {"X-API-KEY": key})
    return _items(_first_list(payload, "organic", "results"), limit)


def _tavily(query: str, limit: int) -> list[dict]:
    key = _credential("TAVILY_API_KEY", "tavily_api_key")
    if not key:
        return []
    body = {"api_key": key, "query": query, "max_results": limit, "search_depth": "basic"}
    return _items(_first_list(_post_json("https://api.tavily.com/search", body), "results"), limit)


_SEARCH = {
    "duckduckgo": _duckduckgo,
    "searxng": _searxng,
    "brave": _brave,
    "serper": _serper,
    "tavily": _tavily,
}


def search(query: str, limit: int = 5) -> list[dict]:
    """Return ``{title, url, snippet}`` hits; any provider failure falls back to DuckDuckGo."""
    q = str(query).strip()
    if not q:
        return []
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 5
    if count <= 0:
        return []
    count = min(20, count)
    provider = _provider()
    try:
        results = _SEARCH[provider](q, count)
    except Exception:  # noqa: BLE001
        results = []
    if not results and provider != "duckduckgo":
        try:
            results = _duckduckgo(q, count)
        except Exception:  # noqa: BLE001
            results = []
    return results[:count]


def _public_host(host: str) -> bool:
    """Cheap SSRF guard: reject obvious local/private names and all private IP literals."""
    name = str(host).strip().lower().rstrip(".")
    if not name or name in LOCAL_NAMES or name.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def fetch_url(url: str, limit_chars: int = 4000) -> dict:
    """Read a public http(s) page and return its title and plain text."""
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        if parsed.scheme.lower() not in ("http", "https"):
            return {"ok": False, "error": "only http/https URLs are allowed"}
        host = parsed.hostname or ""
        if not host:
            return {"ok": False, "error": "URL has no host"}
        if not config.get("search_allow_private", False) and not _public_host(host):
            return {"ok": False, "error": "private or local hosts are not allowed"}
        req = urllib.request.Request(parsed.geturl(), headers={"User-Agent": USER_AGENT})
        with _open(req) as resp:
            raw = resp.read(1_000_000)
            headers = getattr(resp, "headers", None)
            content_type = headers.get("Content-Type", "") if headers else ""
        page = raw.decode("utf-8", "ignore")
        match = TITLE_RE.search(page)
        title = _strip_tags(match.group(1)) if match else host
        text = _html_text(page) if "html" in str(content_type).lower() else " ".join(page.split())
        try:
            count = max(1, min(100_000, int(limit_chars)))
        except (TypeError, ValueError):
            count = 4000
        return {"ok": True, "title": title[:200], "text": text[:count]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
