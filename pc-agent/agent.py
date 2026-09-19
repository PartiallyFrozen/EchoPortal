"""PC stats agent for the Echo Spot dashboard (v2).

Samples CPU / memory / GPU / disks / network / top processes once a second and
broadcasts them as JSON to every connected WebSocket client on the LAN.
Run:  python agent.py   (listens on ws://0.0.0.0:8765)

Sources:
  psutil                  CPU, RAM, per-process CPU/RAM, per-disk IO, per-NIC IO
  Windows perf counters   GPU engine utilisation (3D / Copy / VideoEncode / VideoDecode)
                          GPU adapter memory (dedicated + shared), per-process VRAM,
                          per-physical-disk active %, real CPU clock
  NVML (nvidia-ml-py)     GPU temperature, power, clocks, fan, VRAM, pstate
"""
import asyncio
import base64
import json
import os
import platform
import re
import socket
import subprocess
import time
import winreg

import psutil
import websockets

import voice
import comfy
import ticker
import weatherart
import claudemon

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
TOP_N = 8


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
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append({
                "pid": p.info["pid"], "name": p.info["name"] or "?",
                "cpu": round(p.cpu_percent(None) / psutil.cpu_count(), 1),
                "mem_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
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


COMFY = None
TICKER = None
WEATHER_ART = None
CLAUDE = None


async def _claude_broadcast(data: dict):
    if CLIENTS:
        msg = json.dumps({"ch": "claude", "data": data})
        await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)


async def _claude_notify(title: str, text: str, face: str, chime: bool):
    msg = json.dumps({"ch": "notify", "data": {"title": title, "text": text, "face": face, "ttl": 8000, "chime": chime}})
    await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)
BG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "background.jpg")


def _bg_payload(push: bool = False) -> dict:
    """The user's background picture for the Spot (480x480 JPEG written by the tray app), or None."""
    image = None
    if os.path.exists(BG_FILE):
        with open(BG_FILE, "rb") as f:
            image = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    return {"image": image, "push": push}


async def _weatherart_broadcast(data: dict):
    if CLIENTS:
        msg = json.dumps({"ch": "weatherart", "data": data})
        await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)


async def _ticker_broadcast(data: dict):
    if CLIENTS:
        msg = json.dumps({"ch": "ticker", "data": data})
        await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)


async def _comfy_broadcast(data: dict):
    if CLIENTS:
        msg = json.dumps({"ch": "comfy", "data": data})
        await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)


async def _comfy_notify(title: str, text: str, face: str):
    msg = json.dumps({"ch": "notify", "data": {"title": title, "text": text, "face": face, "ttl": 8000, "chime": True}})
    print("notify:", title, text)
    await asyncio.gather(*(c.send(msg) for c in list(CLIENTS)), return_exceptions=True)


async def handler(ws):
    CLIENTS.add(ws)
    print(f"client connected: {ws.remote_address}  ({len(CLIENTS)} total)")
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
    session = voice.VoiceSession(ws)
    loop = asyncio.get_running_loop()
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                session.on_binary(bytes(msg))          # PCM audio from the Spot
                continue
            # Any client may publish on a channel; relay to everyone else (notify.py, later: voice).
            try:
                obj = json.loads(msg)
            except json.JSONDecodeError:
                continue
            ch = obj.get("ch")
            if ch == "bg" and (obj.get("data") or {}).get("op") in ("reload", "clear"):
                # the tray app wrote (or removed) background.jpg: push it to every Spot
                if (obj.get("data") or {}).get("op") == "clear" and os.path.exists(BG_FILE):
                    os.remove(BG_FILE)
                print("background:", "cleared" if not os.path.exists(BG_FILE) else "%d KB" % (os.path.getsize(BG_FILE) // 1024))
                bmsg = json.dumps({"ch": "bg", "data": _bg_payload(push=True)})
                await asyncio.gather(*(c.send(bmsg) for c in list(CLIENTS) if c is not ws), return_exceptions=True)
            elif ch == "weatherart" and (obj.get("data") or {}).get("op") == "regen" and WEATHER_ART is not None:
                WEATHER_ART.request_regen()
            elif ch == "weatherart" and (obj.get("data") or {}).get("op") == "next" and WEATHER_ART is not None:
                WEATHER_ART.request_next()
            elif ch == "comfy" and (obj.get("data") or {}).get("op") == "test_output" and COMFY is not None:
                asyncio.create_task(COMFY.test_output((obj.get("data") or {}).get("filename", "")))
            elif ch == "voice":
                try:
                    await session.on_message(obj.get("data") or {}, loop)
                except Exception as e:  # noqa: BLE001
                    print("voice error:", repr(e))
                    try:
                        await session.reply(op="error", text=str(e)[:60])
                    except Exception:  # noqa: BLE001
                        pass
            elif ch == "notify":
                print("notify:", obj.get("data"))
                await asyncio.gather(*(c.send(msg) for c in list(CLIENTS) if c is not ws), return_exceptions=True)
    except websockets.exceptions.ConnectionClosed:
        pass  # client vanished without a close frame (app restart, Wi-Fi drop) - normal
    finally:
        CLIENTS.discard(ws)
        print(f"client left: {ws.remote_address}  ({len(CLIENTS)} total)")


async def broadcaster():
    loop = asyncio.get_running_loop()
    while True:
        t0 = time.time()
        try:
            payload = json.dumps({"ch": "stats", "data": await loop.run_in_executor(None, sample)})
        except Exception as e:  # noqa: BLE001
            print("sample error:", e)
            await asyncio.sleep(INTERVAL)
            continue
        if CLIENTS:
            await asyncio.gather(*(c.send(payload) for c in list(CLIENTS)), return_exceptions=True)
        await asyncio.sleep(max(0.0, INTERVAL - (time.time() - t0)))


def _preload_whisper():
    try:
        voice.load_model()
        import numpy as np
        voice.transcribe_pcm(np.zeros(16000, dtype=np.int16).tobytes(), 16000)   # CUDA warm-up
        print("whisper: warm")
    except Exception as e:  # noqa: BLE001
        print("whisper preload failed:", e)


async def main():
    global COMFY, TICKER, WEATHER_ART, CLAUDE
    import os
    import threading
    TICKER = ticker.TickerWatcher(_ticker_broadcast)
    asyncio.create_task(TICKER.run())
    WEATHER_ART = weatherart.WeatherArt(_weatherart_broadcast, lambda: COMFY.base if (COMFY is not None and COMFY.state.get("up")) else None)
    asyncio.create_task(WEATHER_ART.run())
    CLAUDE = claudemon.ClaudeMon(_claude_broadcast, _claude_notify)
    asyncio.create_task(CLAUDE.run())
    threading.Thread(target=_preload_whisper, name="whisper-preload", daemon=True).start()
    ips = [a.address for n in psutil.net_if_addrs().values() for a in n
           if a.family == socket.AF_INET and not a.address.startswith("127.")]
    COMFY = comfy.ComfyWatcher(_comfy_broadcast, _comfy_notify)
    COMFY.lan_ip = next((ip for ip in ips if ip.startswith("192.168.")), ips[0] if ips else "127.0.0.1")
    asyncio.create_task(COMFY.run())
    # static media server for transcoded clips (http://<lan-ip>:8766/media/<file>)
    from aiohttp import web
    os.makedirs(comfy.MEDIA_DIR, exist_ok=True)
    app = web.Application()
    app.router.add_static("/media/", comfy.MEDIA_DIR, show_index=False)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", comfy.MEDIA_PORT).start()
    print(f"media server on http://{COMFY.lan_ip}:{comfy.MEDIA_PORT}/media/")
    ips = [a.address for n in psutil.net_if_addrs().values() for a in n
           if a.family == socket.AF_INET and not a.address.startswith("127.")]
    print(f"EchoPortal agent v3 on ws://0.0.0.0:{PORT}   LAN IPs: {', '.join(ips)}")
    print(f"CPU: {CPU_NAME} | disks: {[d['letters'] for d in DISKS]} | NVML: {'yes' if _NVML else 'no'} | PDH: {'yes' if PDH.ok else 'no'}")
    async with websockets.serve(handler, "0.0.0.0", PORT, max_size=None):
        await broadcaster()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
