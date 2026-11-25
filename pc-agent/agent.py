"""PC stats agent for the Echo Spot dashboard (v2).

Samples CPU / memory / GPU / disks / network / top processes once a second and
broadcasts them as JSON to every connected WebSocket client on the LAN.
Run:  python agent.py   (listens on ws://0.0.0.0:8765)

Sources:
  psutil                  CPU, RAM, per-process CPU/RAM, per-disk IO, per-NIC IO
  Windows perf counters   GPU engine utilisation (3D / Copy / VideoEncode / VideoDecode)
                          GPU adapter memory (dedicated + shared), per-process VRAM,
                          per-physical-disk active %, real CPU clock
  NVML (nvidia-ml-py)     NVIDIA GPU temperature, power, clocks, fan, VRAM, pstate
  ADL (atiadlxx.dll)      AMD GPU temperature, power, clocks, fan, VRAM (see amd.py)
"""
import asyncio
import atexit
import base64
import functools
import hmac
import json
import math
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import winreg

import psutil
import websockets
from aiohttp import web

import amd
import config
import voice
import comfy
import ticker
import weatherart
import claudemon
import alerts
import calendar_ics
import sabres_schedule
import discovery
import timers
import typer
import assistant
import tts
import robot
import memory
import briefing
import homeassistant
import dashboard
import ui_custom

try:
    import win32pdh
except ImportError:  # pragma: no cover
    win32pdh = None
try:
    import pynvml
    pynvml.nvmlInit()
    _NVML = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    pynvml = None
    _NVML = None

PORT = 8765
INTERVAL = 1.0
# With no Spot connected and no metric alert rules there is nothing to sample for: back off to
# this instead of burning a PDH/psutil pass every second for the life of the machine.
IDLE_INTERVAL = 5.0
TOP_N = 8
# The Spot sends 640-byte PCM frames and small JSON. A cap keeps a broken client from making the
# agent buffer an unbounded frame in memory.
MAX_WS_FRAME = 1 << 20
# A timer longer than a week is almost certainly a typo, and it would outlive a reboot or two.
MAX_TIMER_SECS = 7 * 24 * 3600

# Long-running asyncio tasks. Holding a reference matters: the docs warn that a task with no
# strong reference can be garbage-collected mid-flight, which would silently stop the clock.
TASKS: set = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    TASKS.add(task)

    def _done(t):
        TASKS.discard(t)
        if t.cancelled():
            return
        e = t.exception()
        if e is not None:
            print("task failed:", repr(e))

    task.add_done_callback(_done)
    return task


# --------------------------------------------------------------------------- static info
def cpu_name() -> str:
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor()


def disk_map() -> list[dict]:
    """[{idx, letters, model, kind, total_gb}] for every physical disk with a drive letter."""
    ps = (
        "Get-Partition | Where-Object DriveLetter | ForEach-Object { "
        "$d = Get-Disk -Number $_.DiskNumber; $pd = Get-PhysicalDisk | Where-Object DeviceId -eq $_.DiskNumber; "
        "[pscustomobject]@{ idx=$_.DiskNumber; letter=[string]$_.DriveLetter; model=$d.FriendlyName; "
        "media=[string]$pd.MediaType; bus=[string]$pd.BusType; size=$d.Size } } | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
        rows = json.loads(out) if out.strip() else []
        if isinstance(rows, dict):
            rows = [rows]
    except Exception:  # noqa: BLE001
        rows = []
    disks: dict[int, dict] = {}
    for r in rows:
        d = disks.setdefault(int(r["idx"]), {
            "idx": int(r["idx"]), "letters": [], "model": r["model"],
            "kind": f'{r["media"] or "Disk"} ({r["bus"]})', "total_gb": round(int(r["size"]) / 2**30, 1),
        })
        d["letters"].append(r["letter"] + ":")
    for d in disks.values():
        d["letters"].sort()
    return [disks[i] for i in sorted(disks)]


CPU_NAME = cpu_name()
DISKS = disk_map()
HOST = socket.gethostname()
_freq = psutil.cpu_freq()
NOMINAL_MHZ = (_freq.max or _freq.current or 0) if _freq else 0


# --------------------------------------------------------------------------- perf counters
class Pdh:
    """One PDH query holding every wildcard counter we need; collected once per tick."""

    PATHS = {
        "gpu_engine": r"\GPU Engine(*)\Utilization Percentage",
        "gpu_ded": r"\GPU Adapter Memory(*)\Dedicated Usage",
        "gpu_shared": r"\GPU Adapter Memory(*)\Shared Usage",
        "gpu_proc": r"\GPU Process Memory(*)\Dedicated Usage",
        "disk_idle": r"\PhysicalDisk(*)\% Idle Time",
        "cpu_perf": r"\Processor Information(_Total)\% Processor Performance",
    }

    def __init__(self):
        self.ok = win32pdh is not None
        self.h = {}
        if not self.ok:
            return
        self.q = win32pdh.OpenQuery()
        for k, p in self.PATHS.items():
            try:
                self.h[k] = win32pdh.AddEnglishCounter(self.q, p)
            except Exception:  # noqa: BLE001
                pass
        win32pdh.CollectQueryData(self.q)  # prime rate counters

    def read(self) -> dict:
        if not self.ok:
            return {}
        try:
            win32pdh.CollectQueryData(self.q)
        except Exception:  # noqa: BLE001
            return {}
        out = {}
        for k, h in self.h.items():
            try:
                if k == "cpu_perf":
                    out[k] = win32pdh.GetFormattedCounterValue(h, win32pdh.PDH_FMT_DOUBLE)[1]
                else:
                    out[k] = win32pdh.GetFormattedCounterArray(h, win32pdh.PDH_FMT_DOUBLE)  # {instance: value}
            except Exception:  # noqa: BLE001
                pass
        return out


PDH = Pdh()
AMD = amd.AmdGpu() if _NVML is None else None   # one vendor path; NVML wins on NVIDIA
if AMD is not None:
    atexit.register(AMD.close)                  # ADL crashes in its DLL unload without this
_ENG_RE = re.compile(r"engtype_(\w+)$")
_PID_RE = re.compile(r"^pid_(\d+)_")


def gpu_sample(pdh: dict) -> dict:
    g: dict = {"name": "GPU"}
    # -- engine utilisation, summed per engine type (this is what Task Manager shows)
    eng = {"3D": 0.0, "Copy": 0.0, "VideoEncode": 0.0, "VideoDecode": 0.0, "Compute": 0.0}
    for inst, v in pdh.get("gpu_engine", {}).items():
        m = _ENG_RE.search(inst)
        if m and m.group(1) in eng:
            eng[m.group(1)] += v
    g.update({
        "util_3d": round(min(eng["3D"], 100), 1), "util_copy": round(min(eng["Copy"], 100), 1),
        "util_enc": round(min(eng["VideoEncode"], 100), 1), "util_dec": round(min(eng["VideoDecode"], 100), 1),
        "util_compute": round(min(eng["Compute"], 100), 1),
    })
    # -- adapter memory: take the adapter with the most dedicated memory in use
    ded = pdh.get("gpu_ded", {})
    best = max(ded, key=ded.get) if ded else None
    g["ded_used_gb"] = round(ded.get(best, 0) / 2**30, 2) if best else 0
    g["shared_used_gb"] = round(pdh.get("gpu_shared", {}).get(best, 0) / 2**30, 2) if best else 0
    g["shared_total_gb"] = round(psutil.virtual_memory().total * 0.8 / 2**30, 1)
    # -- per-process VRAM (top 5)
    procs: dict[int, float] = {}
    for inst, v in pdh.get("gpu_proc", {}).items():
        m = _PID_RE.match(inst)
        if m and v > 0:
            pid = int(m.group(1))
            procs[pid] = procs.get(pid, 0) + v
    top = []
    for pid, b in sorted(procs.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = f"pid {pid}"
        top.append({"pid": pid, "name": name, "mem_mb": round(b / 2**20)})
    g["procs"] = top
    # -- NVML details
    if _NVML is not None:
        try:
            g["name"] = pynvml.nvmlDeviceGetName(_NVML)
            mem = pynvml.nvmlDeviceGetMemoryInfo(_NVML)
            g["ded_total_gb"] = round(mem.total / 2**30, 1)
            g["temp_c"] = pynvml.nvmlDeviceGetTemperature(_NVML, pynvml.NVML_TEMPERATURE_GPU)
            g["power_w"] = round(pynvml.nvmlDeviceGetPowerUsage(_NVML) / 1000, 1)
            g["power_limit_w"] = round(pynvml.nvmlDeviceGetEnforcedPowerLimit(_NVML) / 1000)
            g["clock_sm"] = pynvml.nvmlDeviceGetClockInfo(_NVML, pynvml.NVML_CLOCK_SM)
            g["clock_sm_max"] = pynvml.nvmlDeviceGetMaxClockInfo(_NVML, pynvml.NVML_CLOCK_SM)
            g["clock_mem"] = pynvml.nvmlDeviceGetClockInfo(_NVML, pynvml.NVML_CLOCK_MEM)
            u = pynvml.nvmlDeviceGetUtilizationRates(_NVML)
            g["util_nvml"] = u.gpu
            g["util_membus"] = u.memory
            try:
                g["fan_pct"] = pynvml.nvmlDeviceGetFanSpeed(_NVML)
            except pynvml.NVMLError:
                g["fan_pct"] = None
            g["pstate"] = f"P{pynvml.nvmlDeviceGetPerformanceState(_NVML)}"
        except pynvml.NVMLError:
            pass
    # -- ADL details (AMD)
    elif AMD is not None and AMD.available:
        a = AMD.sample()
        if a:
            g["name"] = a.get("name") or "GPU"
            if a.get("vram_total_gb"):
                g["ded_total_gb"] = a["vram_total_gb"]
            g["temp_c"] = a.get("temp_c")
            g["power_w"] = a.get("power_w")
            g["clock_sm"] = a.get("clock_sm")
            g["clock_mem"] = a.get("clock_mem")
            g["fan_pct"] = a.get("fan_pct")
            g["util_membus"] = a.get("util_mem")
    return g


# --------------------------------------------------------------------------- sampling
for p in psutil.process_iter():
    try:
        p.cpu_percent(None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
psutil.cpu_percent(None)
_last_net = psutil.net_io_counters(pernic=True)
_last_disk = psutil.disk_io_counters(perdisk=True)
_last_t = time.time()


def sample() -> dict:
    global _last_net, _last_disk, _last_t
    now = time.time()
    dt = max(now - _last_t, 1e-3)
    pdh = PDH.read()

    # -- processes
    # Do not read p.info here: psutil caches Process objects globally and another thread
    # (claudemon) iterates with different attrs, which would drop keys mid-loop.
    procs = []
    ncpu = psutil.cpu_count() or 1
    for p in psutil.process_iter():
        try:
            with p.oneshot():
                name = p.name() or "?"
                rss = p.memory_info().rss
                cpu = p.cpu_percent(None) / ncpu
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        procs.append({
            "pid": p.pid, "name": name,
            "cpu": round(cpu, 1), "mem_mb": round(rss / 1048576),
        })
    procs = [p for p in procs if p["name"] != "System Idle Process"]
    procs.sort(key=lambda x: (x["cpu"], x["mem_mb"]), reverse=True)

    # -- cpu
    perf = pdh.get("cpu_perf")
    mhz = NOMINAL_MHZ * perf / 100 if perf else NOMINAL_MHZ
    cpu = {
        "name": CPU_NAME, "total": psutil.cpu_percent(None), "per_core": psutil.cpu_percent(None, percpu=True),
        "freq_ghz": round(mhz / 1000, 2), "base_ghz": round(NOMINAL_MHZ / 1000, 2),
        "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(),
    }

    # -- memory
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    mem = {
        "used_gb": round(vm.used / 2**30, 1), "total_gb": round(vm.total / 2**30, 1), "pct": vm.percent,
        "avail_gb": round(vm.available / 2**30, 1),
        "committed_gb": round((vm.total - vm.available + sw.used) / 2**30, 1),
        "commit_limit_gb": round((vm.total + sw.total) / 2**30, 1),
    }

    # -- disks (psutil perdisk keys are 'PhysicalDrive0'..; PDH instances are '0 d:' ..)
    disk_io = psutil.disk_io_counters(perdisk=True)
    idle = pdh.get("disk_idle", {})
    disks = []
    for d in DISKS:
        key = f"PhysicalDrive{d['idx']}"
        cur, prev = disk_io.get(key), _last_disk.get(key)
        rd = wr = 0.0
        if cur and prev:
            rd = (cur.read_bytes - prev.read_bytes) / dt / 2**20
            wr = (cur.write_bytes - prev.write_bytes) / dt / 2**20
        idle_v = next((v for k, v in idle.items() if k.split(" ")[0] == str(d["idx"])), None)
        try:
            used_pct = psutil.disk_usage(d["letters"][0] + "\\").percent
        except OSError:
            used_pct = 0
        disks.append({**d, "letters": " ".join(d["letters"]),
                      "active_pct": round(max(0.0, 100 - idle_v), 1) if idle_v is not None else None,
                      "read_mbs": round(rd, 2), "write_mbs": round(wr, 2), "used_pct": used_pct})

    # -- network (only adapters that are up and have an IPv4)
    net_io = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    nets = []
    for name, io in net_io.items():
        st = stats.get(name)
        ip = next((a.address for a in addrs.get(name, []) if a.family == socket.AF_INET), None)
        if not st or not st.isup or not ip or ip.startswith("127.") or name.lower().startswith(("loopback", "vethernet")):
            continue
        prev = _last_net.get(name)
        if not prev:
            continue
        nets.append({
            "name": name, "ip": ip, "speed_mbps": st.speed,
            "down_mbps": round((io.bytes_recv - prev.bytes_recv) * 8 / dt / 1e6, 3),
            "up_mbps": round((io.bytes_sent - prev.bytes_sent) * 8 / dt / 1e6, 3),
        })
    nets.sort(key=lambda n: n["down_mbps"] + n["up_mbps"], reverse=True)

    data = {
        "v": 2, "ts": int(now * 1000), "host": HOST, "uptime_s": int(now - psutil.boot_time()),
        "proc_count": len(procs),
        "cpu": cpu, "mem": mem, "gpu": gpu_sample(pdh), "disks": disks, "nets": nets,
        "top": procs[:TOP_N],
    }
    _last_net, _last_disk, _last_t = net_io, disk_io, now
    return data


# --------------------------------------------------------------------------- server
CLIENTS: set = set()
# Socket -> Spot id from the handshake. Lets assistant turns reach only the Spot that asked, while
# still reaching unlabelled clients such as the browser dashboard.
WS_DEVICES: dict = {}
SEND_TIMEOUT = 2.0
LAST_SAMPLE_TS = 0.0
LAST_STATS: dict = {}
MEDIA_RUNNER = None


COMFY = None
TICKER = None
WEATHER_ART = None
CLAUDE = None
TIMERS = None
CALENDAR = None
ALERTS = None
BRIEFING = None
DISCOVERY = None
ASSISTANT = None
ROBOT = None
STORE = None
HA = None
# Device id -> last-seen info. Populated from the ?device=<uuid> WebSocket handshake and the
# assistant ops, so the local dashboard can show which Spot is talking to this PC.
DEVICES: dict = {}
# Sentence-level TTS clips collected while a turn streams, keyed by device (token fallback).
STREAM_AUDIO: dict[str, list[str]] = {}
# AssistantEngine keeps turn-scoped state on the instance (pending confirmations, undo stack,
# trace). Serialize turns so two Spots cannot overwrite each other's state mid-tool-call.
_ASSISTANT_LOCK = None
_ASSISTANT_LOCK_LOOP = None


def _assistant_lock():
    """A Lock bound to the running loop; tests and the service can use different loops."""
    global _ASSISTANT_LOCK, _ASSISTANT_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _ASSISTANT_LOCK is None or _ASSISTANT_LOCK_LOOP is not loop:
        _ASSISTANT_LOCK = asyncio.Lock()
        _ASSISTANT_LOCK_LOOP = loop
    return _ASSISTANT_LOCK


def face_on(name: str) -> bool:
    """True when this server-side worker should run. An empty list means "run everything"."""
    faces = config.get("faces", [])
    if not isinstance(faces, list) or not faces:
        return True
    return name in {str(f).strip().lower() for f in faces}


def _client_device(client) -> str:
    return str(WS_DEVICES.get(client) or "")


def _assistant_recipients(device: str) -> list:
    """The asking Spot plus unlabelled dashboard clients; never another Spot's socket."""
    device = str(device or "")
    if not device:
        return list(CLIENTS)
    asking = [c for c in CLIENTS if _client_device(c) == device]
    dashboard = [c for c in CLIENTS if not _client_device(c)]
    # dict.fromkeys keeps order and removes a client that somehow appears in both lists.
    return list(dict.fromkeys(asking + dashboard))


async def _send_one(client, msg: str):
    """A stalled socket must not hold up broadcasts to every other client."""
    try:
        await asyncio.wait_for(client.send(msg), SEND_TIMEOUT)
    except Exception:  # noqa: BLE001 - a dead peer is removed; the turn continues
        CLIENTS.discard(client)
        WS_DEVICES.pop(client, None)


async def _send_many(clients, msg: str):
    targets = list(dict.fromkeys(clients))
    if targets:
        await asyncio.gather(*(_send_one(c, msg) for c in targets))


async def _broadcast(ch: str, data: dict):
    """One channel payload to the intended clients. Every worker uses this."""
    if not CLIENTS:
        return
    msg = json.dumps({"ch": ch, "data": data})
    clients = list(CLIENTS)
    if ch == "assistant":
        device = str(data.get("device") or "")
        if device:
            clients = _assistant_recipients(device)
    await _send_many(clients, msg)


async def _notify(title: str, text: str, face: str | None = None, chime: bool = True, ttl: int = 8000):
    print("notify:", title, text)
    await _broadcast("notify", {"title": title, "text": text, "face": face, "ttl": ttl, "chime": chime})


# The watchers take a one-argument async callable; bind the channel name once instead of writing
# six identical wrappers.
_timer_broadcast = functools.partial(_broadcast, "timer")
_calendar_broadcast = functools.partial(_broadcast, "calendar")
_ticker_broadcast = functools.partial(_broadcast, "ticker")
_weatherart_broadcast = functools.partial(_broadcast, "weatherart")
_comfy_broadcast = functools.partial(_broadcast, "comfy")
_claude_broadcast = functools.partial(_broadcast, "claude")
_assistant_broadcast = functools.partial(_broadcast, "assistant")

# User-writable, so it lives next to config.json rather than in PyInstaller's _internal folder.
BG_FILE = config.data_path("background.jpg")


def _bg_payload(push: bool = False) -> dict:
    """The user's background picture for the Spot (480x480 JPEG written by the tray app), or None."""
    image = None
    if os.path.exists(BG_FILE):
        with open(BG_FILE, "rb") as f:
            image = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    return {"image": image, "push": push}


def _hub_token() -> str:
    return (config.get("api_token", "") or "").strip()


def _supplied_ws_token(ws) -> str:
    """Token from `X-EchoPortal-Token`, a Bearer header, or ?token= on the WebSocket URL."""
    # websockets >= 14 hands the handler a ServerConnection with .request; older releases expose
    # the handshake as .request_headers/.path. Support both so a dependency bump cannot lock the
    # Spot out (or, worse, silently disable the check).
    request = getattr(ws, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    if headers is None:
        headers = getattr(ws, "request_headers", None)
    path = (getattr(request, "path", None) if request is not None else None) or getattr(ws, "path", "") or ""
    if headers is not None:
        supplied = headers.get("X-EchoPortal-Token", "")
        if supplied:
            return supplied.strip()
        auth = headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    except ValueError:
        return ""
    return (query.get("token") or [""])[0].strip()


def _tokens_equal(supplied: str, expected: str) -> bool:
    # hmac.compare_digest(str, str) rejects non-ASCII tokens; config.json is user-editable UTF-8.
    return hmac.compare_digest(str(supplied).encode("utf-8"), str(expected).encode("utf-8"))


def _token_ok(supplied: str) -> bool:
    expected = _hub_token()
    return not expected or _tokens_equal(supplied, expected)


def _supplied_device(ws) -> str:
    """The Spot's persistent device id from ?device=<uuid> on the WebSocket URL."""
    request = getattr(ws, "request", None)
    # aiohttp's request.path drops the query string; path_qs keeps it.
    raw = ""
    if request is not None:
        raw = (getattr(request, "path_qs", "") or getattr(request, "raw_path", "")
               or getattr(request, "path", "") or "")
    if not raw:
        raw = getattr(ws, "path", "") or ""
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
    except ValueError:
        return ""
    return str((query.get("device") or [""])[0])[:64]


def _track_device(device: str, ws=None) -> str:
    """Remember which Spot is talking, with a friendly name from assistant_devices."""
    key = str(device or "").strip()
    if not key:
        return ""
    configured = config.get("assistant_devices", {}) or {}
    prefs = configured.get(key, {}) if isinstance(configured, dict) else {}
    name = prefs.get("name") or prefs.get("label") or key[:8] if isinstance(prefs, dict) else key[:8]
    info = dict(DEVICES.get(key) or {})
    info.update({"id": key, "name": str(name)[:64], "last_seen": int(time.time())})
    if ws is not None:
        info["remote"] = str(getattr(ws, "remote_address", "") or "")
    DEVICES[key] = info
    return key


# --------------------------------------------------------------------------- assistant tools
def _tool_timer(text: str):
    return TIMERS.handle_text(str(text)) if TIMERS is not None else {"text": "timers unavailable"}


def _tool_calendar() -> dict:
    if CALENDAR is None:
        return {"events": [], "configured": False}
    snap = CALENDAR.snapshot()
    events = snap.get("events") or []
    return {"configured": bool(CALENDAR.urls), "count": len(events),
            "next": [{"title": e.get("title") or e.get("summary"), "start": e.get("start")} for e in events[:5]]}


def _tool_pc_stats() -> dict:
    global LAST_SAMPLE_TS, LAST_STATS
    # The broadcast sampler is idle until a Spot or an alert rule needs it. A direct question
    # still deserves live numbers, so take one sample on demand and reuse it for the next turn.
    if not LAST_STATS:
        try:
            LAST_STATS = sample()
            LAST_SAMPLE_TS = time.time()
        except Exception as e:  # noqa: BLE001 - the model gets a plain failure, not a traceback
            return {"error": str(e)}
    cpu, mem, gpu = LAST_STATS.get("cpu", {}), LAST_STATS.get("mem", {}), LAST_STATS.get("gpu", {})
    return {"cpu_pct": cpu.get("total"), "mem_pct": mem.get("pct"), "gpu": gpu.get("name"),
            "gpu_temp_c": gpu.get("temp_c"), "vram_gb": gpu.get("ded_total_gb"),
            "uptime_s": LAST_STATS.get("uptime_s")}


def _tool_notify(title: str, text: str):
    return _notify(str(title)[:80], str(text)[:200], "assistant")


def _tool_json(value, fallback=None):
    """Models often pass structured arguments as a JSON string; accept both forms."""
    if value is None or value == "":
        return fallback
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def _build_assistant_tools() -> dict:
    """The only capabilities the model can reach. Everything else is unavailable by design."""
    tools = {
        "search_notes": assistant.search_notes,
        "web_search": assistant.web_search,
        "web_fetch": assistant.web_fetch,
        "save_draft": assistant.save_draft,
        "timer": _tool_timer,
        "calendar": _tool_calendar,
        "sabres_schedule": lambda query="": sabres_schedule.lookup(str(query)),
        "pc_stats": _tool_pc_stats,
        "notify": _tool_notify,
        "type_text": lambda text, enter=True: typer.type_text(str(text), bool(enter), "focused"),
        "open_app": assistant.open_app,
        "run_command": assistant.run_command,
        "robot": lambda action: ROBOT.send(action) if ROBOT is not None else {"ok": False, "error": "robot unavailable"},
        "remember": lambda text, category="fact": STORE.remember(str(text), str(category)),
        "recall": lambda query, limit=5: STORE.recall(str(query), limit),
        "forget": lambda text: STORE.forget(str(text)),
        "look_at_screen": assistant.look_at_screen,
        "ha_state": lambda entity_id="": HA.get_states(str(entity_id) or None),
        "ha_call": lambda domain, service, entity_id="", data=None: HA.call_service(
            str(domain), str(service), str(entity_id) or None, _tool_json(data, {})),
        "task_add": assistant.task_add,
        "task_list": assistant.task_list,
        "calendar_add": assistant.calendar_add,
        "window_list": lambda: typer.list_windows(),
        "window_focus": lambda title: typer.focus_window(str(title)),
        "window_close": lambda title: typer.close_window(str(title)),
        "media_control": lambda action: typer.media_control(str(action)),
        "undo": lambda: ASSISTANT.undo_last() if ASSISTANT is not None else {
            "ok": False, "error": "assistant unavailable"},
    }
    # Memory tools need the store to exist; main() creates it before the engine.
    if STORE is None:
        for name in ("remember", "recall", "forget"):
            tools.pop(name, None)
    # Do not advertise the robot (and ask for confirmation) when MQTT is not configured.
    if ROBOT is None or not ROBOT.configured():
        tools.pop("robot", None)
    return tools


async def _assistant_send(ws, data: dict):
    if ws is None:
        return
    await _send_one(ws, json.dumps({"ch": "assistant", "data": data}))


async def _assistant_emit(event: dict):
    """Broadcast progress; turn sentences into audio clips as soon as they are complete."""
    op = str(event.get("op") or "")
    token = str(event.get("token") or "")
    device = str(event.get("device") or "")
    if op == "stream_reset":
        # A provider failed after streaming partial speech. Drop its clips before the fallback
        # starts so the Spot never plays a partial answer followed by the full one.
        STREAM_AUDIO.pop(device or token, None)
        await _broadcast("assistant", event)
        return
    if op == "speak" and config.get("assistant_speak", True) and config.get("assistant_stream_tts", True):
        text = str(event.get("text") or "").strip()
        if text and token and not (ASSISTANT is not None and ASSISTANT.is_cancelled(token)):
            try:
                audio = await asyncio.to_thread(tts.speak, text, device)
            except Exception as e:  # noqa: BLE001
                print("stream tts failed:", e)
                audio = None
            if audio:
                STREAM_AUDIO.setdefault(device or token, []).append(audio)
                await _broadcast("assistant", {"op": "audio", "audio": audio, "token": token,
                                               "device": device, "segment": True})
    await _broadcast("assistant", event)


async def _finish_assistant(result: dict, ws=None) -> dict:
    """Publish a finished turn. The requesting Spot gets it directly; others see it too."""
    token = str(result.get("token") or "")
    key = str(result.get("device") or token)
    segments = STREAM_AUDIO.pop(key, []) if key else []
    if segments:
        result = {**result, "audio": segments[0], "audio_segments": segments,
                  "streamed_audio": True}
    elif result.get("op") == "reply" and config.get("assistant_speak", True):
        try:
            audio = await asyncio.to_thread(tts.speak, result.get("text", ""),
                                            str(result.get("device") or ""))
        except Exception as e:  # noqa: BLE001
            print("tts failed:", e)
            audio = None
        if audio:
            result = {**result, "audio": audio}
    if result.get("op") == "reply" and config.get("assistant_speak", True):
        await asyncio.to_thread(tts.cleanup)
    if "trace" not in result and ASSISTANT is not None:
        result = {**result, "trace": list(dict.fromkeys(getattr(ASSISTANT, "turn_trace", [])))}
    msg = json.dumps({"ch": "assistant", "data": result})
    if ws is None:
        await _broadcast("assistant", result)
    else:
        await _assistant_send(ws, result)
        others = [c for c in _assistant_recipients(key) if c is not ws]
        await _send_many(others, msg)
    return result


async def _assistant_ask(text: str, ws=None, device: str = ""):
    if ASSISTANT is None:
        await _assistant_send(ws, {"op": "error", "text": "assistant unavailable", "device": device})
        return
    async with _assistant_lock():
        key = _track_device(device, ws) or "assistant"
        STREAM_AUDIO.pop(key, None)
        try:
            result = await ASSISTANT.ask(text, emit=_assistant_emit, device=key)
            await _finish_assistant(result, ws)
        except Exception as e:  # noqa: BLE001
            print("assistant error:", repr(e))
            await _assistant_send(ws, {"op": "error", "text": str(e)[:200], "device": key})
            await _broadcast("assistant", {"op": "error", "text": str(e)[:200], "device": key})


async def _assistant_confirm(token: str, yes: bool, ws=None, always: bool = False, device: str = ""):
    if ASSISTANT is None:
        await _assistant_send(ws, {"op": "error", "text": "assistant unavailable", "device": device})
        return
    async with _assistant_lock():
        key = _track_device(device, ws)
        try:
            result = await ASSISTANT.confirm(token, yes, emit=_assistant_emit,
                                            always=always, device=key)
            await _finish_assistant(result, ws)
        except Exception as e:  # noqa: BLE001
            print("assistant confirm error:", repr(e))
            await _assistant_send(ws, {"op": "error", "text": str(e)[:200], "device": key})
            await _broadcast("assistant", {"op": "error", "text": str(e)[:200], "device": key})


async def _assistant_cancel(token: str = "", ws=None, device: str = ""):
    if ASSISTANT is None:
        return
    key = _track_device(device, ws)
    ASSISTANT.cancel(token)
    await _assistant_send(ws, {"op": "cancelled", "token": token, "device": key})


async def _assistant_from_voice(text: str, device: str = ""):
    """Called by voice.py when the user spoke with the Jarvis target selected.

    The device must be the Spot's real id, not a literal "voice": AssistantFace ignores
    assistant events whose device does not match, which otherwise drops the spoken reply.
    """
    if ASSISTANT is None:
        return None
    async with _assistant_lock():
        key = str(device or "voice")[:64]
        STREAM_AUDIO.pop(key, None)
        result = await ASSISTANT.ask(text, emit=_assistant_emit, device=key)
        await _finish_assistant(result)
        return {"text": result.get("text", "")}


async def handler(ws):
    # The hub can type into the focused Windows window (voice.send). main() calls ensure_token(),
    # so a running agent normally has a token; reject any handshake that cannot prove it.
    if not _token_ok(_supplied_ws_token(ws)):
        print(f"client rejected (bad token): {ws.remote_address}")
        await ws.close(code=1008, reason="unauthorized")
        return
    CLIENTS.add(ws)
    device_id = _supplied_device(ws)
    WS_DEVICES[ws] = device_id
    if device_id:
        _track_device(device_id, ws)
    print(f"client connected: {ws.remote_address}  ({len(CLIENTS)} total)"
          + (f"  device={device_id[:8]}" if device_id else ""))
    if COMFY is not None:
        try:
            await ws.send(json.dumps({"ch": "comfy", "data": COMFY.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if TICKER is not None and TICKER.items:
        try:
            await ws.send(json.dumps({"ch": "ticker", "data": TICKER.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if WEATHER_ART is not None and WEATHER_ART.image_b64:
        try:
            await ws.send(json.dumps({"ch": "weatherart", "data": WEATHER_ART.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if os.path.exists(BG_FILE):
        try:
            await ws.send(json.dumps({"ch": "bg", "data": _bg_payload()}))
        except Exception:  # noqa: BLE001
            pass
    if CLAUDE is not None:
        try:
            await ws.send(json.dumps({"ch": "claude", "data": CLAUDE.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if TIMERS is not None:
        try:
            await ws.send(json.dumps({"ch": "timer", "data": TIMERS.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if CALENDAR is not None and CALENDAR.urls:
        try:
            await ws.send(json.dumps({"ch": "calendar", "data": CALENDAR.snapshot()}))
        except Exception:  # noqa: BLE001
            pass
    if ASSISTANT is not None:
        await _assistant_send(ws, {"op": "status", **ASSISTANT.status()})
    session = voice.VoiceSession(ws, device_id)
    loop = asyncio.get_running_loop()
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                session.on_binary(bytes(msg))          # PCM audio from the Spot
                continue
            # Any client may publish on a channel; relay to everyone else (notify.py, later: voice).
            try:
                obj = json.loads(msg)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(obj, dict):   # a JSON array or scalar is not a message
                continue
            ch = obj.get("ch")
            if ch == "bg" and (obj.get("data") or {}).get("op") in ("reload", "clear"):
                # the tray app wrote (or removed) background.jpg: push it to every Spot
                if (obj.get("data") or {}).get("op") == "clear" and os.path.exists(BG_FILE):
                    os.remove(BG_FILE)
                print("background:", "cleared" if not os.path.exists(BG_FILE) else "%d KB" % (os.path.getsize(BG_FILE) // 1024))
                bmsg = json.dumps({"ch": "bg", "data": _bg_payload(push=True)})
                await _send_many([c for c in CLIENTS if c is not ws], bmsg)
            elif ch == "weatherart" and (obj.get("data") or {}).get("op") == "regen" and WEATHER_ART is not None:
                WEATHER_ART.request_regen()
            elif ch == "weatherart" and (obj.get("data") or {}).get("op") == "next" and WEATHER_ART is not None:
                WEATHER_ART.request_next()
            elif ch == "comfy" and (obj.get("data") or {}).get("op") == "test_output" and COMFY is not None:
                _spawn(COMFY.test_output((obj.get("data") or {}).get("filename", "")))
            elif ch == "timer" and TIMERS is not None:
                try:
                    result = await TIMERS.handle_message(obj.get("data") or {})
                except Exception as e:  # noqa: BLE001
                    print("timer error:", repr(e))
                    result = {"text": "timer error"}
                try:
                    await ws.send(json.dumps({"ch": "timer", "data": {"op": "ack", **result}}))
                except Exception:  # noqa: BLE001
                    pass
            elif ch == "calendar" and CALENDAR is not None and (obj.get("data") or {}).get("op") == "refresh":
                if not CALENDAR.request_refresh():
                    # No feeds configured: answer instead of silently doing nothing.
                    try:
                        await ws.send(json.dumps({"ch": "calendar", "data": CALENDAR.snapshot()}))
                    except Exception:  # noqa: BLE001
                        pass
            elif ch == "voice":
                try:
                    await session.on_message(obj.get("data") or {}, loop)
                except Exception as e:  # noqa: BLE001
                    print("voice error:", repr(e))
                    try:
                        await session.reply(op="error", text=str(e)[:60])
                    except Exception:  # noqa: BLE001
                        pass
            elif ch == "assistant" and ASSISTANT is not None:
                adata = obj.get("data") or {}
                aop = adata.get("op")
                device = str(adata.get("device") or device_id or "")[:64]
                if aop == "ask":
                    _spawn(_assistant_ask(str(adata.get("text") or "")[:2000], ws, device))
                elif aop == "confirm":
                    _spawn(_assistant_confirm(str(adata.get("token") or ""), bool(adata.get("yes")),
                                              ws, bool(adata.get("always")), device))
                elif aop == "cancel":
                    _spawn(_assistant_cancel(str(adata.get("token") or ""), ws, device))
                elif aop == "status":
                    await _assistant_send(ws, {"op": "status", **ASSISTANT.status(), "device": device})
            elif ch == "notify":
                print("notify:", obj.get("data"))
                await _send_many([c for c in CLIENTS if c is not ws], msg)
    except websockets.exceptions.ConnectionClosed:
        pass  # client vanished without a close frame (app restart, Wi-Fi drop) - normal
    finally:
        CLIENTS.discard(ws)
        WS_DEVICES.pop(ws, None)
        print(f"client left: {ws.remote_address}  ({len(CLIENTS)} total)")


def _needs_stats() -> bool:
    """True when a Spot is listening or a metric alert rule needs the numbers."""
    if CLIENTS:
        return True
    return bool(ALERTS and any(isinstance(r, dict) and "metric" in r for r in ALERTS.rules))


async def broadcaster():
    global LAST_SAMPLE_TS, LAST_STATS
    loop = asyncio.get_running_loop()
    while True:
        if not _needs_stats():
            # No client and no metric rule: service checks run on their own 15 s loop, and the
            # timers/calendar workers do not need stats. Sampling here would just burn CPU.
            await asyncio.sleep(IDLE_INTERVAL)
            continue
        t0 = time.time()
        try:
            stats = await loop.run_in_executor(None, sample)
            payload = json.dumps({"ch": "stats", "data": stats})
        except Exception as e:  # noqa: BLE001
            print("sample error:", e)
            await asyncio.sleep(INTERVAL)
            continue
        LAST_SAMPLE_TS = time.time()
        LAST_STATS = stats
        if CLIENTS:
            await _send_many(list(CLIENTS), payload)
        if ALERTS is not None:
            try:
                await ALERTS.check(stats)
            except Exception as e:  # noqa: BLE001
                print("alert error:", e)
        await asyncio.sleep(max(0.0, INTERVAL - (time.time() - t0)))


def _preload_whisper():
    # Gemini needs no local model; just report the choice so the log says what voice will do.
    print(voice.status())
    if voice.backend() == "gemini":
        return
    try:
        voice.load_model()
        import numpy as np
        voice.transcribe_pcm(np.zeros(16000, dtype=np.int16).tobytes(), 16000)   # CUDA warm-up
        print("whisper: warm")
    except Exception as e:  # noqa: BLE001
        print("whisper preload failed:", e)


def _authorized(request) -> bool:
    """Shared token for the HTTP API and /media/.

    The browser dashboard gets a same-origin HttpOnly cookie so theme, plugin and audio URLs do
    not have to carry the long-lived token in visible/browser-history URLs. Native clients still
    use the header and MediaPlayer still uses ?token=.
    """
    token = _hub_token()
    if not token:
        return True
    supplied = request.headers.get("X-EchoPortal-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not supplied:
        supplied = request.query.get("token", "")
    if not supplied:
        supplied = request.cookies.get("echoportal_token", "")
    return _tokens_equal(supplied, token)


@web.middleware
async def _media_auth(request, handler):
    """The static media server can expose ComfyUI outputs, so it follows the same token rule."""
    if request.path.startswith("/media/") and not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return await handler(request)


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def http_notify(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    title = str(data.get("title") or "EchoPortal")[:80]
    text = str(data.get("text") or data.get("message") or "")[:200]
    face = data.get("face")
    face = str(face)[:24] if face else None
    ttl = int(_safe_float(data.get("ttl"), 8000))
    ttl = max(500, min(ttl, 600_000))
    chime = bool(data.get("chime", True))
    await _notify(title, text, face, chime, ttl)
    return web.json_response({"ok": True, "clients": len(CLIENTS)})


async def http_status(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    state = ASSISTANT.status() if ASSISTANT else {}
    return web.json_response({
        "ok": True, "version": 3, "host": HOST, "clients": len(CLIENTS), "voice": voice.backend(),
        "backend": state.get("backend", "none"),
        "model_choice": state.get("model_choice", ""),
        "model": state.get("model", ""),
        "ready": bool(state.get("ready")),
        "devices": len(DEVICES),
        "media_port": comfy.MEDIA_PORT,
        "hub_port": PORT,
        "token_required": bool(_hub_token()),
        "faces": config.get("faces", []),
        "clip": config.get("media_clip", "fireplace.mp4"),
        "sample_age_s": round(time.time() - LAST_SAMPLE_TS, 1) if LAST_SAMPLE_TS else None,
        "timers": TIMERS.snapshot() if TIMERS else {"items": []},
        "calendar": {
            "configured": bool(CALENDAR.urls) if CALENDAR else False,
            "events": len(CALENDAR.events) if CALENDAR else 0,
            "error": CALENDAR.error if CALENDAR else None,
        },
        "alerts": len(ALERTS.rules) if ALERTS else 0,
        "assistant": state or None,
        "briefing": BRIEFING.snapshot() if BRIEFING else None,
        "robot": bool(ROBOT and ROBOT.configured()),
    })


async def http_timers(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({"ok": True, **(TIMERS.snapshot() if TIMERS else {"items": [], "now": int(time.time() * 1000)})})


async def http_timer(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if TIMERS is None:
        return web.json_response({"ok": False, "error": "timers unavailable"}, status=503)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    label = str(data.get("label") or "Timer")[:48]
    wake = bool(data.get("wake"))
    try:
        if data.get("at") or data.get("hour") is not None:
            if data.get("at"):
                h, _, m = str(data["at"]).partition(":")
                hour, minute = int(h), int(m or 0)
            else:
                hour, minute = int(data["hour"]), int(data.get("minute") or 0)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return web.json_response(
                    {"ok": False, "error": "hour must be 0-23 and minute 0-59"}, status=400)
            item = TIMERS.add_alarm(hour, minute, label, wake)
        else:
            seconds = data.get("seconds")
            if seconds is None and data.get("minutes") is not None:
                seconds = float(data["minutes"]) * 60
            if seconds is None and data.get("hours") is not None:
                seconds = float(data["hours"]) * 3600
            seconds = float(seconds) if seconds is not None else 0.0
            if not math.isfinite(seconds) or seconds <= 0:
                return web.json_response({"ok": False, "error": "seconds, minutes, hours or at is required"}, status=400)
            if seconds > MAX_TIMER_SECS:
                return web.json_response(
                    {"ok": False, "error": "timer is too long (max %d days)" % (MAX_TIMER_SECS // 86400)},
                    status=400)
            item = TIMERS.add_timer(int(seconds), label, wake)
    except (TypeError, ValueError, OverflowError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    await TIMERS.changed()
    return web.json_response({"ok": True, "item": item, "timer": TIMERS.snapshot()})


async def http_timer_cancel(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if TIMERS is None:
        return web.json_response({"ok": False, "error": "timers unavailable"}, status=503)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    op = "clear" if data.get("all") else ("dismiss" if data.get("dismiss") else "cancel")
    result = await TIMERS.handle_message({"op": op, "id": data.get("id")})
    return web.json_response({"ok": True, "result": result, "timer": TIMERS.snapshot()})


async def http_calendar(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if CALENDAR is None:
        return web.json_response({"ok": True, "events": [], "configured": False, "updated": 0, "error": None})
    if request.query.get("refresh"):
        CALENDAR.request_refresh()
    return web.json_response({"ok": True, **CALENDAR.snapshot()})


async def http_calendar_refresh(request):
    from aiohttp import web
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if CALENDAR is not None:
        CALENDAR.request_refresh()
    return web.json_response({"ok": True})


async def http_assistant(request):
    """POST {"text": "..."} to ask Jarvis without the Spot; useful for scripts and testing."""
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response({"ok": False, "error": "assistant unavailable"}, status=503)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    text = str(data.get("text") or "")[:2000]
    if not text:
        return web.json_response({"ok": False, "error": "text is required"}, status=400)
    device = _track_device(str(data.get("device") or "api")) or "api"
    async with _assistant_lock():
        STREAM_AUDIO.pop(device, None)
        result = await _finish_assistant(await ASSISTANT.ask(
            text, device=device, dry_run=bool(data.get("dry_run", False))))
    return web.json_response({"ok": True, **result})


def _assistant_limit(request, default: int = 50) -> int:
    try:
        return max(1, min(500, int(request.query.get("limit", default))))
    except (TypeError, ValueError):
        return default


async def http_assistant_metrics(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response([])
    return web.json_response(ASSISTANT.metrics_snapshot(_assistant_limit(request)))


async def http_assistant_history(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response([])
    return web.json_response(ASSISTANT.history_snapshot(_assistant_limit(request)))


async def http_assistant_delete_turn(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response({"ok": False, "error": "assistant unavailable"}, status=503)
    try:
        turn_id = int(request.match_info.get("id", ""))
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "invalid chat id"}, status=400)
    return web.json_response({"ok": True, **ASSISTANT.delete_turn(turn_id)})


async def http_assistant_clear_history(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response({"ok": False, "error": "assistant unavailable"}, status=503)
    return web.json_response({"ok": True, **ASSISTANT.clear_history()})


async def http_assistant_facts(request):
    """List remembered facts so the dashboard can offer thumbs up/down feedback."""
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if STORE is None:
        return web.json_response([])
    facts = await asyncio.to_thread(STORE.all_facts, _assistant_limit(request, 100))
    facts.sort(key=lambda f: (-int(f.get("feedback", 0)), -int(f.get("uses", 0)), -int(f["id"])))
    return web.json_response(facts)


async def http_assistant_feedback(request):
    """POST {"fact_id": 12, "vote": "up"|"down"} to change a fact's recall weight."""
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if STORE is None:
        return web.json_response({"ok": False, "error": "memory unavailable"}, status=503)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = await asyncio.to_thread(STORE.set_feedback, data.get("fact_id"), str(data.get("vote", "")))
    if not result.get("ok"):
        return web.json_response(result, status=400)
    return web.json_response({"ok": True, **result})


async def http_assistant_models(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    choices = await asyncio.to_thread(assistant.available_model_choices)
    return web.json_response({"ok": True, "choices": choices,
                              "current": assistant.current_model_choice()})


async def http_assistant_model(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    value = data.get("id", data.get("model", ""))
    result = assistant.set_model_choice(value)
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def http_assistant_devices(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response(DEVICES)


async def http_assistant_device(request):
    """Read or update per-device model/voice preferences without touching other settings."""
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    device = str(request.query.get("device") or "").strip()
    values = {}
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        if isinstance(data, dict):
            device = str(data.get("device") or device).strip()
            values = data.get("values", data)
    if not device:
        return web.json_response({"ok": False, "error": "device is required"}, status=400)
    if request.method == "POST":
        result = assistant.set_device_preferences(device, values)
        return web.json_response(result, status=200 if result.get("ok") else 400)
    return web.json_response({"ok": True, "device": device, **assistant.device_preferences(device)})


async def http_assistant_cancel(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if ASSISTANT is None:
        return web.json_response({"ok": False, "error": "assistant unavailable"}, status=503)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    token = str(data.get("token") or "")
    ASSISTANT.cancel(token)
    return web.json_response({"ok": True, "token": token})


async def http_dashboard(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    state = ASSISTANT.status() if ASSISTANT else {}
    status = {
        "backend": state.get("backend", "none"),
        "ready": bool(state.get("ready")),
        "voice_backend": voice.backend(),
        "host": HOST,
        "version": 3,
        "clients": len(CLIENTS),
        "devices": len(DEVICES),
        "hub_port": PORT,
        "media_port": comfy.MEDIA_PORT,
        "assistant": state,
    }
    page = dashboard.render(status, PORT, comfy.MEDIA_PORT, ui_custom.plugin_names())
    response = web.Response(text=page, content_type="text/html", charset="utf-8")
    token = _hub_token()
    if token:
        # Same-site, HttpOnly: subresources authenticate without a token in their URLs, and page
        # scripts cannot read the token back out of the cookie.
        response.set_cookie("echoportal_token", token, max_age=86400, httponly=True,
                            samesite="Strict", path="/")
    return response


async def http_ui_theme(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.Response(text=ui_custom.read_theme(), content_type="text/css", charset="utf-8")


async def http_ui_plugin(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    name = request.match_info.get("name", "")
    body = ui_custom.read_plugin(name)
    if body is None:
        return web.Response(status=404, text="not found")
    return web.Response(text=body, content_type="application/javascript", charset="utf-8")


def _openai_last_user(messages) -> str:
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, dict) or str(message.get("role")) != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content or "")[:2000]
    return ""


# conversation key -> pending confirmation token, for OpenAI clients that cannot show our buttons.
OPENAI_PENDING: dict = {}


async def _openai_answer(text: str, conversation: str, emit=None) -> dict:
    if ASSISTANT is None:
        return {"op": "error", "text": "assistant unavailable"}
    async with _assistant_lock():
        pending = OPENAI_PENDING.get(conversation)
        answer = None
        if pending:
            reply = str(text).strip().lower()
            if reply in ("yes", "y", "ok", "approve", "approved"):
                answer = await ASSISTANT.confirm(pending["token"], True, emit=emit)
            elif reply in ("always", "always yes"):
                answer = await ASSISTANT.confirm(pending["token"], True, emit=emit, always=True)
            elif reply in ("no", "n", "deny", "cancel", "decline"):
                answer = await ASSISTANT.confirm(pending["token"], False, emit=emit)
            if answer is not None:
                OPENAI_PENDING.pop(conversation, None)
        if answer is None:
            STREAM_AUDIO.pop("openwebui", None)
            answer = await ASSISTANT.ask(text, emit=emit, device="openwebui")
        if answer.get("op") == "confirm":
            OPENAI_PENDING[conversation] = {"token": answer.get("token", "")}
            answer = {**answer, "op": "reply",
                      "text": str(answer.get("text", "")) + "\n\nReply YES / NO / ALWAYS to approve."}
        return answer


async def http_openai_models(request):
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({
        "object": "list",
        "data": [{"id": "jarvis", "object": "model", "owned_by": "echoportal"}],
    })


async def http_openai_chat(request):
    """OpenAI-compatible /v1/chat/completions so Open WebUI can drive the same brain."""
    if not _authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    text = _openai_last_user(data.get("messages"))
    conversation = str(data.get("user") or data.get("conversation_id") or "openwebui")[:64]
    model = str(data.get("model") or "jarvis")[:64]
    created = int(time.time())
    completion_id = "chatcmpl-" + os.urandom(6).hex()

    if not data.get("stream"):
        result = await _openai_answer(text, conversation)
        return web.json_response({
            "id": completion_id, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": str(result.get("text") or "")},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    queue: asyncio.Queue = asyncio.Queue()
    async def emit(event):
        if event.get("op") == "stream":
            await queue.put(str(event.get("text") or ""))

    task = asyncio.ensure_future(_openai_answer(text, conversation, emit))
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    def chunk(content: str, finish=None) -> bytes:
        payload = {
            "id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": content} if content else {},
                         "finish_reason": finish}],
        }
        return ("data: " + json.dumps(payload) + "\n\n").encode("utf-8")

    sent = False
    while not task.done() or not queue.empty():
        try:
            piece = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            continue
        sent = True
        await response.write(chunk(piece))
    result = await task
    if not sent:
        await response.write(chunk(str(result.get("text") or "")))
    await response.write(chunk("", finish="stop"))
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def shutdown():
    """Stop the workers and close the HTTP server so a tray restart cannot race on port 8766."""
    global MEDIA_RUNNER
    pending = [t for t in TASKS if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    runner = MEDIA_RUNNER
    MEDIA_RUNNER = None
    if runner is not None:
        try:
            await runner.cleanup()
        except Exception as e:  # noqa: BLE001
            print("media server cleanup:", e)


def _briefing_context() -> dict:
    """Inputs for the scheduled briefing; failures simply leave that section out."""
    context = {}
    if CALENDAR is not None:
        try:
            context["calendar"] = CALENDAR.snapshot()
        except Exception:  # noqa: BLE001
            pass
    if HA is not None and HA.configured():
        try:
            context["home"] = HA.get_states()
        except Exception:  # noqa: BLE001
            pass
    return context


async def _briefing_speak(text: str) -> None:
    """Render the briefing with Piper and push it as a spoken reply to every client."""
    audio = await asyncio.to_thread(tts.speak, text)
    if isinstance(audio, dict):
        audio = audio.get("audio") or audio.get("path") or ""
    if audio:
        await _broadcast("assistant", {"op": "reply", "text": text, "audio": audio,
                                       "audio_segments": [], "token": "briefing", "device": ""})


async def _home_state_poll() -> None:
    """Refresh the prompt's home snapshot once a minute when HA is configured."""
    while True:
        try:
            if HA is not None and HA.configured():
                snapshot = await asyncio.to_thread(HA.get_states)
                assistant.set_home_context(briefing.home_summary(snapshot))
            else:
                assistant.set_home_context("")
        except Exception as exc:  # noqa: BLE001 - never let a poll failure kill the worker
            print("home context refresh failed: %s" % exc)
        await asyncio.sleep(60)


async def main():
    global COMFY, TICKER, WEATHER_ART, CLAUDE, TIMERS, CALENDAR, ALERTS, DISCOVERY, MEDIA_RUNNER
    global ASSISTANT, ROBOT, STORE, HA, BRIEFING
    import threading
    # Bind LAN ports only after auth exists. A fresh install gets a generated token; a read-only
    # empty-token config aborts here instead of exposing remote keystroke injection.
    config.ensure_token()
    TIMERS = timers.TimerStore(_timer_broadcast, _notify)
    _spawn(TIMERS.run())
    CALENDAR = calendar_ics.CalendarWatcher(_calendar_broadcast)
    _spawn(CALENDAR.run())
    ALERTS = alerts.AlertEngine(_notify)
    _spawn(ALERTS.run())
    BRIEFING = briefing.BriefingScheduler(_notify, _briefing_speak, _briefing_context)
    if BRIEFING.enabled:
        _spawn(BRIEFING.run())
    _spawn(_home_state_poll())
    voice.COMMAND_HANDLER = TIMERS.handle_text
    ROBOT = robot.RobotClient()
    STORE = memory.default_store()
    HA = homeassistant.HomeAssistantClient()
    ASSISTANT = assistant.AssistantEngine(_build_assistant_tools(), broadcast=_assistant_broadcast,
                                          store=STORE)
    voice.ASSISTANT_HANDLER = _assistant_from_voice if face_on("assistant") else None
    print("assistant:", ASSISTANT.status())
    # Optional workers: the Markets and Claude faces are not registered, so their polling stays
    # off unless the user explicitly adds them to "faces" in config.json.
    if face_on("markets"):
        TICKER = ticker.TickerWatcher(_ticker_broadcast)
        _spawn(TICKER.run())
    if face_on("sky"):
        WEATHER_ART = weatherart.WeatherArt(
            _weatherart_broadcast,
            lambda: COMFY.base if (COMFY is not None and COMFY.state.get("up")) else None,
        )
        _spawn(WEATHER_ART.run())
    if face_on("claude"):
        CLAUDE = claudemon.ClaudeMon(_claude_broadcast, _notify)
        _spawn(CLAUDE.run())
    threading.Thread(target=_preload_whisper, name="whisper-preload", daemon=True).start()
    ips = [a.address for n in psutil.net_if_addrs().values() for a in n
           if a.family == socket.AF_INET and not a.address.startswith("127.")]
    comfy_urls = config.get("comfy_urls") or comfy.DEFAULT_URLS
    if isinstance(comfy_urls, str):
        comfy_urls = [comfy_urls]
    COMFY = comfy.ComfyWatcher(_comfy_broadcast, _notify, urls=comfy_urls)
    # Display only. Registration below deliberately uses discovery's default-route lookup:
    # the first 192.168.* NIC can be a VPN/virtual adapter and advertise an unreachable IP.
    COMFY.lan_ip = discovery._lan_ip() or (ips[0] if ips else "127.0.0.1")
    if face_on("comfy"):
        _spawn(COMFY.run())
    # static media server for transcoded clips (http://<lan-ip>:8766/media/<file>)
    os.makedirs(comfy.MEDIA_DIR, exist_ok=True)
    ui_custom.ensure()
    app = web.Application(middlewares=[_media_auth])
    app.router.add_post("/notify", http_notify)
    app.router.add_post("/api/notify", http_notify)
    app.router.add_get("/api/status", http_status)
    app.router.add_get("/api/timers", http_timers)
    app.router.add_post("/api/timer", http_timer)
    app.router.add_post("/api/timer/cancel", http_timer_cancel)
    app.router.add_get("/api/calendar", http_calendar)
    app.router.add_post("/api/calendar/refresh", http_calendar_refresh)
    app.router.add_post("/api/assistant", http_assistant)
    app.router.add_get("/api/assistant/metrics", http_assistant_metrics)
    app.router.add_get("/api/assistant/history", http_assistant_history)
    app.router.add_delete("/api/assistant/history", http_assistant_clear_history)
    app.router.add_delete("/api/assistant/history/{id}", http_assistant_delete_turn)
    app.router.add_get("/api/assistant/facts", http_assistant_facts)
    app.router.add_post("/api/assistant/feedback", http_assistant_feedback)
    app.router.add_get("/api/assistant/models", http_assistant_models)
    app.router.add_post("/api/assistant/model", http_assistant_model)
    app.router.add_get("/api/assistant/devices", http_assistant_devices)
    app.router.add_get("/api/assistant/device", http_assistant_device)
    app.router.add_post("/api/assistant/device", http_assistant_device)
    app.router.add_post("/api/assistant/cancel", http_assistant_cancel)
    app.router.add_get("/dashboard", http_dashboard)
    app.router.add_get("/ui", http_dashboard)
    app.router.add_get("/ui/theme.css", http_ui_theme)
    app.router.add_get("/ui/plugins/{name}", http_ui_plugin)
    app.router.add_get("/v1/models", http_openai_models)
    app.router.add_post("/v1/chat/completions", http_openai_chat)
    app.router.add_static("/media/", comfy.MEDIA_DIR, show_index=False)
    MEDIA_RUNNER = web.AppRunner(app, access_log=None)
    await MEDIA_RUNNER.setup()
    await web.TCPSite(MEDIA_RUNNER, "0.0.0.0", comfy.MEDIA_PORT).start()
    print(f"media server on http://{COMFY.lan_ip}:{comfy.MEDIA_PORT}/media/")
    print(f"HTTP API on http://{COMFY.lan_ip}:{comfy.MEDIA_PORT}/api/status")
    if _hub_token():
        print("hub: token required (set the same token on the Spot)")

    async def advertise():
        # Advertise off the startup path: probing can take seconds on a busy/odd network and must
        # never delay the WebSocket hub coming up.
        global DISCOVERY
        loop = asyncio.get_running_loop()
        try:
            DISCOVERY = await loop.run_in_executor(
                None, discovery.register, PORT, comfy.MEDIA_PORT, None
            )
        except Exception as e:  # noqa: BLE001
            print("discovery: registration failed:", repr(e))

    _spawn(advertise())
    ips = [a.address for n in psutil.net_if_addrs().values() for a in n
           if a.family == socket.AF_INET and not a.address.startswith("127.")]
    print(f"EchoPortal agent v3 on ws://0.0.0.0:{PORT}   LAN IPs: {', '.join(ips)}")
    print(f"CPU: {CPU_NAME} | disks: {[d['letters'] for d in DISKS]} | NVML: {'yes' if _NVML else 'no'} | "
          f"AMD: {'yes' if (AMD is not None and AMD.available) else 'no'} | PDH: {'yes' if PDH.ok else 'no'}")
    try:
        async with websockets.serve(handler, "0.0.0.0", PORT, max_size=MAX_WS_FRAME):
            await broadcaster()
    finally:
        # Also runs when the hub bind fails, so a tray restart cannot leave the media
        # site bound on 8766 after the old process exits.
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        # Withdraw the mDNS record so the Spot does not keep a stale address for the TTL.
        try:
            discovery.unregister(DISCOVERY)
        except Exception:  # noqa: BLE001
            pass
        # ADL's DLL teardown faults during normal interpreter shutdown; close the
        # context and leave before that runs.
        if AMD is not None:
            AMD.close()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)
