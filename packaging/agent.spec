# PyInstaller spec for the EchoPortal agent (Windows).
#
#   pip install -r pc-agent/requirements.txt -r requirements-dev.txt
#   pyinstaller --noconfirm packaging/agent.spec
#
# Produces dist/EchoPortalAgent/ (one directory, no console window). Inno Setup turns that into an
# installer; see packaging/installer.iss.
#
# Voice is deliberately not bundled: faster-whisper and CUDA would add gigabytes, and the model
# downloads itself on first use. voice.py imports it lazily, so the build works without it and only
# the voice face complains.
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
AGENT = os.path.join(ROOT, "pc-agent")

datas = [
    (os.path.join(ROOT, "packaging", "config.example.json"), "packaging"),
    # Piper neural TTS: standalone runtime + British male model, ~85 MB, no Python packages.
    (os.path.join(ROOT, "packaging", "piper"), "piper"),
    (os.path.join(AGENT, "demo_prompt.json"), "."),
    (os.path.join(AGENT, "weatherart", "library"), os.path.join("weatherart", "library")),
]
datas = [(src, dst) for src, dst in datas if os.path.exists(src)]

hiddenimports = [
    "win32pdh", "win32api", "win32con", "win32gui", "win32process", "win32clipboard",
    # Jarvis TTS, robot and vision/search imports are lazy, so PyInstaller cannot see them on its own.
    "pythoncom", "win32com.client", "paho.mqtt.client",
    "search_providers", "screenshot", "mcp_client", "briefing", "sabres_schedule",
    "pynvml", "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "dateutil", "tzdata",
]


def _no_tests(name: str) -> bool:
    """collect_submodules would otherwise drag every package's test suite into the exe."""
    return not ({"test", "tests"} & set(name.split(".")))


hiddenimports += collect_submodules("pystray") \
    + collect_submodules("icalendar", filter=_no_tests) \
    + collect_submodules("recurring_ical_events", filter=_no_tests) \
    + collect_submodules("zeroconf")

excludes = [
    "faster_whisper", "ctranslate2", "torch", "onnxruntime", "av",
    "matplotlib", "pandas", "scipy", "IPython", "notebook", "pytest",
]

a = Analysis(
    [os.path.join(AGENT, "tray.py")],
    pathex=[AGENT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = os.path.join(ROOT, "packaging", "echoportal.ico")
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EchoPortalAgent",
    debug=False,
    strip=False,
    upx=False,
    console=False,                      # tray app: no console window
    icon=icon if os.path.exists(icon) else None,
    version=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EchoPortalAgent",
)
