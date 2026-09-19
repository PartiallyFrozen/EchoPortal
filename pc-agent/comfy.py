"""ComfyUI channel for the EchoPortal agent.

Follows ComfyUI's own WebSocket (/ws) and publishes a compact job state on channel "comfy"
whenever it changes, plus a downscaled copy of each new output image. Reconnects forever,
so it is harmless when ComfyUI is not running.

State message ({"ch":"comfy","data":{...}}):
  up            bool   ComfyUI reachable
  running       bool
  queue         int    jobs waiting (not counting the running one)
  node          str    title of the node currently executing
  node_class    str
  step, steps   int    progress inside the current node (sampler steps etc.)
  nodes_done, nodes_total  int   coarse job progress
  elapsed       float  seconds since the job started
  eta           float|None  seconds remaining, from the previous job's duration
  last_duration float|None
  last_image    str|None   "data:image/jpeg;base64,..."  (400x400 max), sent once when it changes
  last_image_name str
  jobs_today    int
  error         str|None
"""
import asyncio
import base64
import io
import json
import time
import uuid

import os
import shutil
import subprocess

import aiohttp

import config

MEDIA_DIR = config.data_path("media")
MEDIA_PORT = 8766
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".gif", ".webp")

DEFAULT_URLS = ["http://127.0.0.1:8188", "http://127.0.0.1:8000"]
THUMB = 400


class ComfyWatcher:
    def __init__(self, broadcast, notify, urls=None):
        self.broadcast = broadcast          # async fn(dict) -> sends {"ch":"comfy","data":dict} to all clients
        self.notify = notify                # async fn(title, text, face) -> banner
        self.urls = urls or DEFAULT_URLS
        self.base = None
        self.state = self._idle_state(up=False)
        self.prompt_nodes = {}              # node id -> title for the running prompt
        self.job_start = None
        self.last_duration = None
        self.jobs_today = 0
        self.last_image_key = None
        self.current_prompt = None
        self._ignored = set()               # prompt ids queued by rook-* clients (weather art)
        self._last_sent = None
        self._last_sent_at = 0.0

    lan_ip = "127.0.0.1"          # set by the agent
    last_video = None             # URL of the latest transcoded clip (persists across publishes)
    last_video_name = ""

    # ------------------------------------------------------------------ video
    @staticmethod
    def is_video(name: str) -> bool:
        return name.lower().endswith(VIDEO_EXT)

    async def _make_clip(self, session, img) -> tuple:
        """Download a video output, transcode to a 480x480 H.264 clip the Spot can decode, return (url, poster_b64)."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            print("comfy: ffmpeg not found; cannot make clip")
            return None, None
        os.makedirs(MEDIA_DIR, exist_ok=True)
        name = img["filename"]
        stem = os.path.splitext(os.path.basename(name))[0]
        src = os.path.join(MEDIA_DIR, "src_" + os.path.basename(name))
        out = os.path.join(MEDIA_DIR, stem + ".mp4")
        poster = os.path.join(MEDIA_DIR, stem + ".jpg")
        try:
            params = {"filename": name, "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
            async with session.get(self.base + "/view", params=params, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    print("comfy: /view %s -> HTTP %d" % (name, r.status))
                    return None, None
                data = await r.read()
            with open(src, "wb") as f:
                f.write(data)
            vf = "crop=min(iw\\,ih):min(iw\\,ih),scale=480:480:flags=lanczos,fps=24"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-vf", vf, "-c:v", "libx264", "-profile:v", "baseline",
                   "-level", "3.1", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-b:v", "900k", "-maxrate", "1200k",
                   "-bufsize", "2M", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", out]
            loop = asyncio.get_running_loop()
            t0 = time.time()
            rc = await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True, creationflags=flags))
            if rc.returncode != 0:
                print("comfy: ffmpeg failed:", rc.stderr[-300:])
                return None, None
            await loop.run_in_executor(None, lambda: subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", out, "-frames:v", "1", "-q:v", "4", poster], capture_output=True, creationflags=flags))
            print("comfy: clip ready %s (%.1fs, %d KB)" % (os.path.basename(out), time.time() - t0, os.path.getsize(out) // 1024))
            url = "http://%s:%d/media/%s" % (self.lan_ip, MEDIA_PORT, os.path.basename(out))
            poster_b64 = None
            if os.path.exists(poster):
                poster_b64 = "data:image/jpeg;base64," + base64.b64encode(open(poster, "rb").read()).decode()
            return url, poster_b64
        except Exception as e:  # noqa: BLE001
            print("comfy: clip failed:", e)
            return None, None
        finally:
            try:
                os.remove(src)
            except OSError:
                pass

    @staticmethod
    def _idle_state(up):
        return {"up": up, "running": False, "queue": 0, "node": "", "node_class": "", "step": 0, "steps": 0,
                "nodes_done": 0, "nodes_total": 0, "elapsed": 0.0, "eta": None, "last_duration": None,
                "last_image": None, "last_image_name": "", "jobs_today": 0, "error": None}

    # ------------------------------------------------------------------ publish
    async def publish(self, force=False, with_image=None):
        s = dict(self.state)
        s["last_duration"] = self.last_duration
        s["jobs_today"] = self.jobs_today
        if self.job_start and s["running"]:
            s["elapsed"] = round(time.time() - self.job_start, 1)
            s["eta"] = round(max(0.0, self.last_duration - s["elapsed"]), 1) if self.last_duration else None
        s["last_image"] = with_image       # only carried when a new image arrives
        s["last_video"] = self.last_video
        s["last_video_name"] = self.last_video_name
        key = json.dumps({k: v for k, v in s.items() if k not in ("elapsed", "eta", "last_image")}, sort_keys=True)
        now = time.time()
        if force or with_image or key != self._last_sent or now - self._last_sent_at > 1.0:
            self._last_sent, self._last_sent_at = key, now
            await self.broadcast(s)

    def snapshot(self):
        """State for a client that just connected (no image payload)."""
        s = dict(self.state); s["last_duration"] = self.last_duration; s["jobs_today"] = self.jobs_today; s["last_image"] = None
        s["last_video"] = self.last_video; s["last_video_name"] = self.last_video_name
        return s

    # ------------------------------------------------------------------ http helpers
    async def _get_json(self, session, path):
        async with session.get(self.base + path, timeout=aiohttp.ClientTimeout(total=5)) as r:
            return await r.json()

    async def _load_prompt_titles(self, session, prompt_id):
        try:
            q = await self._get_json(session, "/queue")
            for entry in q.get("queue_running", []) + q.get("queue_pending", []):
                if len(entry) > 3 and entry[1] == prompt_id and str((entry[3] or {}).get("client_id", "")).startswith("rook-"):
                    self._ignored.add(prompt_id)
                    return
                if len(entry) > 2 and entry[1] == prompt_id:
                    prompt = entry[2]
                    self.prompt_nodes = {nid: (n.get("_meta", {}).get("title") or n.get("class_type", "?"), n.get("class_type", "?"))
                                         for nid, n in prompt.items()}
                    self.state["nodes_total"] = len(self.prompt_nodes)
                    return
        except Exception:  # noqa: BLE001
            pass

    async def _fetch_thumb(self, session, img):
        try:
            from PIL import Image
            params = {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
            async with session.get(self.base + "/view", params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.read()
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((THUMB, THUMB))
            out = io.BytesIO(); im.save(out, "JPEG", quality=80)
            return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
        except Exception as e:  # noqa: BLE001
            print("comfy: thumbnail failed:", e)
            return None

    # ------------------------------------------------------------------ main loop
    async def run(self):
        while True:
            try:
                await self._session()
            except Exception as e:  # noqa: BLE001
                if self.state["up"]:
                    print("comfy: connection lost:", repr(e)[:80])
            if self.state["up"]:
                self.state = self._idle_state(up=False)
                await self.publish(force=True)
            await asyncio.sleep(5)

    async def _session(self):
        async with aiohttp.ClientSession() as session:
            # find a live server
            for base in self.urls:
                try:
                    async with session.get(base + "/system_stats", timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:
                            self.base = base
                            break
                except Exception:  # noqa: BLE001
                    continue
            else:
                return
            print("comfy: connected to", self.base)
            self.state = self._idle_state(up=True)
            try:
                q = await self._get_json(session, "/queue")
                self.state["queue"] = len(q.get("queue_pending", []))
                self.state["running"] = bool(q.get("queue_running"))
            except Exception:  # noqa: BLE001
                pass
            await self.publish(force=True)

            ws_url = self.base.replace("http", "ws", 1) + "/ws?clientId=" + uuid.uuid4().hex
            async with session.ws_connect(ws_url, heartbeat=20) as ws:
                poller = asyncio.create_task(self._poll(session))
                try:
                    await self._ws_loop(session, ws)
                finally:
                    poller.cancel()

    async def _ws_loop(self, session, ws):
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        m = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    if m.get("type") in ("execution_start", "executed", "execution_error", "execution_interrupted"):
                        print("comfy: ws event", m.get("type"), str(m.get("data"))[:80])
                    await self._handle(session, m.get("type"), m.get("data") or {})

    async def _poll(self, session):
        """Every second: /queue tells us the running prompt even when its events go only to the browser.
        Completion is detected when the prompt leaves the queue; outputs come from /history."""
        seen_running = None
        while True:
            await asyncio.sleep(1.0)
            try:
                q = await self._get_json(session, "/queue")
            except Exception:  # noqa: BLE001
                continue
            running = q.get("queue_running") or []
            pending = q.get("queue_pending") or []
            s = self.state
            s["queue"] = len(pending)
            cur = running[0][1] if running and len(running[0]) > 1 else None
            if cur and len(running[0]) > 3 and str((running[0][3] or {}).get("client_id", "")).startswith("rook-"):
                self._ignored.add(cur)
                seen_running = None
                await self.publish()
                continue
            if cur and cur != seen_running:
                # back-to-back jobs: close out the previous one before starting the new one
                if seen_running and s["running"] and self.current_prompt == seen_running:
                    await self._finish_from_history(session, seen_running)
                # a job started that we may not have been told about
                seen_running = cur
                if not s["running"] or self.current_prompt != cur:
                    self.current_prompt = cur
                    s.update(running=True, error=None, step=0, steps=0, nodes_done=0, node="", node_class="")
                    self.job_start = time.time()
                    prompt = running[0][2] if len(running[0]) > 2 else {}
                    self.prompt_nodes = {nid: (n.get("_meta", {}).get("title") or n.get("class_type", "?"), n.get("class_type", "?"))
                                         for nid, n in prompt.items()}
                    s["nodes_total"] = len(self.prompt_nodes)
                    s["node"] = "rendering"
                    print("comfy: job %s running (%d nodes) [poll]" % (cur[:8], s["nodes_total"]))
            elif not cur and seen_running:
                # the job we were watching left the queue -> finished (or failed)
                finished = seen_running
                seen_running = None
                if s["running"]:
                    await self._finish_from_history(session, finished)
            await self.publish()

    async def _finish_from_history(self, session, prompt_id):
        s = self.state
        image = None
        try:
            h = await self._get_json(session, "/history/" + prompt_id)
            entry = h.get(prompt_id, {})
            status = entry.get("status", {})
            ok = status.get("completed", True) and status.get("status_str") != "error"
            imgs = []
            for out in (entry.get("outputs") or {}).values():
                for k in ("images", "gifs", "videos", "video"):
                    v = out.get(k)
                    if isinstance(v, list):
                        imgs += [i for i in v if isinstance(i, dict) and i.get("filename") and i.get("type", "output") == "output"]
            if imgs:
                image = await self._new_output(session, imgs[-1])
        except Exception as e:  # noqa: BLE001
            print("comfy: history lookup failed:", e)
            ok = True
        if self.job_start:
            self.last_duration = round(time.time() - self.job_start, 1)
            self.jobs_today += 1
            await self.notify("ComfyUI", ("job done in %.0f s" % self.last_duration) if ok else "job failed", "comfy")
        s.update(running=False, node="", node_class="", step=0, steps=0)
        s["nodes_done"] = s["nodes_total"]
        if not ok:
            s["error"] = "job failed"
        self.job_start = None
        self.current_prompt = None
        print("comfy: job %s finished [poll] image=%s" % (prompt_id[:8], bool(image)))
        await self.publish(force=True, with_image=image)

    async def _new_output(self, session, img):
        """A new output file appeared: thumbnail for images, transcoded clip (+poster) for videos."""
        key = (img.get("subfolder"), img.get("filename"))
        if key == self.last_image_key:
            return None
        self.last_image_key = key
        name = img.get("filename", "")
        self.state["last_image_name"] = name
        if self.is_video(name):
            url, poster = await self._make_clip(session, img)
            if url:
                self.last_video, self.last_video_name = url, name
            return poster
        self.last_video, self.last_video_name = None, ""      # newest output wins
        return await self._fetch_thumb(session, img)

    async def test_output(self, filename: str):
        """Debug hook: treat an existing file in ComfyUI's output folder as a fresh output."""
        async with aiohttp.ClientSession() as session:
            image = await self._new_output(session, {"filename": filename, "subfolder": "", "type": "output"})
            await self.publish(force=True, with_image=image)

    async def _handle(self, session, t, d):
        s = self.state
        image = None
        pid = d.get("prompt_id")
        if t == "execution_start":
            await self._load_prompt_titles(session, pid)   # learns whether this one is ours to ignore
        if pid and pid in self._ignored:
            if t in ("executing", "execution_success", "execution_error") and (d.get("node") is None or t != "executing"):
                self._ignored.discard(pid)
                s.update(running=False, node="", node_class="", step=0, steps=0)
                self.job_start = None
                self.current_prompt = None
                await self.publish(force=True)
            return
        if t == "status":
            s["queue"] = d.get("status", {}).get("exec_info", {}).get("queue_remaining", 0)
            if s["queue"] == 0 and not s["running"]:
                pass
        elif t == "execution_start":
            s.update(running=True, error=None, step=0, steps=0, nodes_done=0, node="", node_class="")
            self.job_start = time.time()
            self.current_prompt = d.get("prompt_id")
            self.prompt_nodes = {}
            await self._load_prompt_titles(session, d.get("prompt_id"))
        elif t == "executing":
            node = d.get("node")
            if node is None:
                # job finished (the poller handles jobs whose events we never saw)
                if self.job_start:
                    self.last_duration = round(time.time() - self.job_start, 1)
                    self.jobs_today += 1
                    await self.notify("ComfyUI", "job done in %.0f s" % self.last_duration, "comfy")
                s.update(running=False, node="", node_class="", step=0, steps=0)
                s["nodes_done"] = s["nodes_total"]
                self.job_start = None
            else:
                title, cls = self.prompt_nodes.get(str(node), (f"node {node}", "?"))
                s.update(running=True, node=title, node_class=cls, step=0, steps=0)
                s["nodes_done"] += 1
        elif t == "progress":
            s["step"], s["steps"] = d.get("value", 0), d.get("max", 0)
        elif t == "executed":
            out = d.get("output") or {}
            imgs = []
            for k in ("images", "gifs", "videos", "video"):
                v = out.get(k)
                if isinstance(v, list):
                    imgs += [i for i in v if isinstance(i, dict) and i.get("filename")]
            imgs = [i for i in imgs if i.get("type", "output") == "output"] or imgs
            if imgs:
                image = await self._new_output(session, imgs[-1])
        elif t == "execution_error":
            s["error"] = (d.get("exception_message") or "error")[:80]
            s["running"] = False
            self.job_start = None
            await self.notify("ComfyUI error", s["error"][:40], "comfy")
        elif t == "execution_interrupted":
            s.update(running=False, node="", step=0, steps=0)
            self.job_start = None
        await self.publish(force=t in ("execution_start", "executed", "execution_error"), with_image=image)
