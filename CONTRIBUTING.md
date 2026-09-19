# Contributing

Thanks for taking a look. This is a small project with two halves: a Kotlin app for the Echo Spot
and a Python agent for Windows. You can work on either without touching the other.

## Development environment

**Agent (Python 3.12, Windows):**

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r pc-agent/requirements.txt -r requirements-dev.txt
python pc-agent/tray.py          # tray app; use agent.py for a console with logs
```

`requirements-voice.txt` adds faster-whisper and its CUDA dependencies. Skip it unless you are
working on the voice face; everything else runs without it.

**Shell (Kotlin, Android):**

```bash
cd rook   # "rook" is the Echo Spot codename; this directory is the Android app
./gradlew --rerun-tasks assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`--rerun-tasks` is not optional. A warm Gradle daemon here has a habit of reporting success without
recompiling Kotlin, which produces a stale APK and a confusing hour.

You do not need an Echo Spot to work on the agent. You do need one (or an Android device with a
square-ish screen) to see the shell.

## Before you open a pull request

```bash
ruff check .
pytest -q
cd rook && ./gradlew assembleDebug
```

CI runs exactly these. Keep them green.

## Guidelines

- **One face per file.** Faces are self-contained: they declare the channels they want, draw
  themselves onto a canvas, and own their pages. Adding a face should not require changing the
  shell, only registering it in `MainActivity`.
- **The agent owns the data, the device owns the presentation.** Parsing, polling and transcoding
  belong in Python. The Spot is a slow MediaTek chip from 2017, so keep per-frame work cheap.
- **Draw for a circle.** The panel is round and the glass is offset from the framebuffer. Anything
  in the corners is invisible; the calibration values exist for a reason.
- **No new runtime dependency without a reason.** The agent should start in a second or two.
- Commit messages: a short imperative summary line, then why rather than what.
- Please open an issue before a large change, so nobody writes the same thing twice.

## Reporting bugs

Use the issue templates. For anything on the device, `adb logcat -d | grep EchoPortal` is usually the
fastest way to say what happened, and the agent writes `pc-agent/agent.log`. Redact your IP and any
API keys before pasting logs.
