"""The Jarvis UI: a complete chat shell with a theme hook, plugin bus and live endpoints."""
import dashboard

ENDPOINTS = (
    "/api/status", "/api/assistant/metrics", "/api/assistant/history",
    "/api/assistant/models", "/api/assistant/model", "/api/assistant/devices",
    "/api/assistant/device", "/api/assistant/facts", "/api/assistant/feedback",
)


def test_render_is_a_complete_product_page():
    page = dashboard.render(
        {"backend": "hybrid", "ready": True, "voice_backend": "local", "host": "DESKTOP"},
        8765, 8766, ["blob.js", "my-plugin.js"],
    )

    assert page.startswith("<!doctype html>")
    assert page.rstrip().endswith("</html>")
    assert "<title>Jarvis · PC agent</title>" in page
    assert "New conversation" in page
    assert "What can I help you with?" in page
    assert 'id="composer"' in page and 'id="thread"' in page
    assert 'id="model-select"' in page
    assert "Delete all saved chats" in page
    assert "loadModels()" in page and "deleteChat(" in page
    assert "rateFact(" in page and "renderFacts()" in page
    assert 'id="user-theme"' in page
    assert "window.Jarvis = Jarvis" in page
    assert 'data-panel="settings"' in page and 'data-panel="observability"' in page
    assert "function renderSettings()" in page and "function renderObservability(" in page
    assert "var audioQueue" in page and "function pumpAudio()" in page
    assert "onSpeak: 'speak'" in page and "onToolError: 'tool_error'" in page
    assert "Jarvis.plugin" not in page  # plugins are loaded as separate files
    assert 'theme.href = "/ui/theme.css"' in page
    assert "history.replaceState({}, \"\", location.pathname)" in page
    for endpoint in ENDPOINTS:
        assert endpoint in page
    assert '"blob.js", "my-plugin.js"' in page
    assert "setInterval(refreshSide, REFRESH)" in page
    assert "var hubPort = 8765, mediaPort = 8766, REFRESH = 5000;" in page
    assert "<style>" in page and "<script>" in page
    assert "<link" in page  # the editable theme stylesheet
    assert "@import" not in page and "cdn" not in page.lower()
    assert "__PLUGINS__" not in page and "__HUB_PORT__" not in page
    assert "8765" in page and "8766" in page


def test_dynamic_values_are_safe_inside_the_script_tag():
    evil = "</script><script>alert('x')</script>"
    page = dashboard.render({"backend": evil, "ready": False}, plugins=[evil])

    assert evil not in page
    assert "\\u003c/script\\u003e" in page
    assert "&#x27;" not in page  # JSON, not HTML escaping


def test_plugin_names_are_passed_through_unchanged():
    page = dashboard.render({}, plugins=[])
    assert "loadPlugins([])" in page
