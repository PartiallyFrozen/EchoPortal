# Tests

These cover the pure logic in the agent: forecast-to-key mapping, ticker config handling, output
classification, and the Claude Code state machine. They run on any OS in a second or two.

Deliberately not covered here:

- `agent.py`, `tray.py`, `typer.py` and `voice.py` import `pywin32`, `pynvml` and `pystray`, so they
  only import on Windows with a GPU present. CI runs on Linux, so these modules are excluded rather
  than mocked into meaninglessness.
- Anything that talks to ComfyUI, Open-Meteo, Coinbase or Yahoo. Those are integration paths; a test
  that hits them would fail for reasons that have nothing to do with a pull request.
- The Kotlin shell. CI builds it; drawing on a round canvas is checked by looking at it.

Run them with `pytest -q` from the repository root. `pyproject.toml` puts `pc-agent` on the path.
