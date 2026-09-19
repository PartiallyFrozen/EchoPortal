"""One line describing what a tool call is doing, shown under the Claude face's sunburst."""
import claude_hook


def test_bash_prefers_the_description_over_the_command():
    assert claude_hook.detail_for("Bash", {"command": "npm test", "description": "Run the test suite"}) == "Run the test suite"


def test_bash_falls_back_to_the_command():
    assert claude_hook.detail_for("Bash", {"command": "git status"}) == "git status"


def test_file_tools_show_only_the_file_name():
    assert claude_hook.detail_for("Read", {"file_path": "D:/a/b/SettingsPage.tsx"}) == "SettingsPage.tsx"
    assert claude_hook.detail_for("Edit", {"file_path": "/home/u/app/main.py"}) == "main.py"
    assert claude_hook.detail_for("NotebookEdit", {"notebook_path": "/tmp/run.ipynb"}) == "run.ipynb"


def test_search_tools_show_the_pattern():
    assert claude_hook.detail_for("Grep", {"pattern": "ThemeProvider"}) == "ThemeProvider"
    assert claude_hook.detail_for("Glob", {"pattern": "**/*.kt"}) == "**/*.kt"


def test_web_and_skill_tools():
    assert claude_hook.detail_for("WebFetch", {"url": "https://example.com/x"}) == "https://example.com/x"
    assert claude_hook.detail_for("WebSearch", {"query": "echo spot teardown"}) == "echo spot teardown"
    assert claude_hook.detail_for("Skill", {"skill": "code-review"}) == "code-review"


def test_an_unknown_tool_uses_any_descriptive_field():
    assert claude_hook.detail_for("SomeNewTool", {"description": "doing a thing"}) == "doing a thing"
    assert claude_hook.detail_for("SomeNewTool", {"query": "a query"}) == "a query"


def test_missing_or_odd_input_never_raises():
    assert claude_hook.detail_for("Bash", None) == ""
    assert claude_hook.detail_for("Bash", "not a dict") == ""
    assert claude_hook.detail_for("Unknown", {}) == ""


def test_the_line_is_short_enough_for_the_round_screen():
    long_desc = "x" * 500
    assert len(claude_hook.detail_for("Bash", {"description": long_desc})) <= 80
