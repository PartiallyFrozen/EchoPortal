"""Shared-token auth for the HTTP API and the WebSocket handshake."""
import asyncio
from types import SimpleNamespace

import agent
import config

TOKEN = "unit-test-token"


def _request(headers=None, query=None, cookies=None):
    return SimpleNamespace(headers=headers or {}, query=query or {}, cookies=cookies or {})


def test_http_status_is_401_without_a_token(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "api_token": TOKEN})
    response = asyncio.run(agent.http_status(_request()))
    assert response.status == 401


def test_http_auth_accepts_header_bearer_query_and_dashboard_cookie(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "api_token": TOKEN})
    requests = (
        _request(headers={"X-EchoPortal-Token": TOKEN}),
        _request(headers={"Authorization": "Bearer " + TOKEN}),
        _request(query={"token": TOKEN}),
        _request(cookies={"echoportal_token": TOKEN}),
    )
    assert all(agent._authorized(request) for request in requests)
    assert agent._authorized(_request(headers={"X-EchoPortal-Token": "wrong"})) is False


def test_websocket_handshake_accepts_the_encoded_query_token(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "api_token": TOKEN})
    ws = SimpleNamespace(
        request=SimpleNamespace(path="/?device=spot-1&token=" + TOKEN, headers={}),
        path="/",
    )
    assert agent._supplied_ws_token(ws) == TOKEN
    assert agent._token_ok(agent._supplied_ws_token(ws)) is True
    assert agent._token_ok("wrong") is False


def test_dashboard_sets_an_httponly_cookie(monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "api_token": TOKEN})
    monkeypatch.setattr(agent, "ASSISTANT", None)
    monkeypatch.setattr(agent.ui_custom, "plugin_names", lambda: [])
    monkeypatch.setattr(agent.dashboard, "render", lambda *args, **kwargs: "<html></html>")

    response = asyncio.run(agent.http_dashboard(_request(
        headers={"X-EchoPortal-Token": TOKEN})))

    assert response.status == 200
    cookie = response.cookies["echoportal_token"]
    assert cookie.value == TOKEN
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Strict"
