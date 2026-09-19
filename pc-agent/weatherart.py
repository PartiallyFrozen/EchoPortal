"""Weather art for the EchoPortal 'Sky' face: an abstract, placeless picture matching the current weather.

A library of 50 pre-rendered images (10 conditions x 5 times of day) lives in weatherart/library/<key>.jpg
(built by scripts/build_weather_library.py). At runtime the forecast is mapped to a key and the library image
is sent to the Spot - no GPU work. A tap on the face re-paints that key (replacing the library copy); a
missing key is rendered on demand.

Broadcast: {"ch":"weatherart","data":{"image": b64 jpeg|None, "key", "prompt", "temp", "condition", "hi", "lo", "generated", "error"}}
"""
import asyncio
import base64
import io
import json
import os
import random
import time
import uuid
from datetime import datetime

import aiohttp

import config

HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = config.data_path("weatherart", "library")
BUNDLED_LIBRARY = config.bundled_path("weatherart", "library")
TEMPLATE = os.path.join(HERE, "demo_prompt.json")
LAT, LON, TZ = config.location()     # set in config.json; the face stays quiet until it is
PROMPT_NODE = "57:27"      # CLIPTextEncode (positive) in the saved workflow
OUT_SIZE = 480
VARIANTS = 4               # library pictures per key: <key>.jpg, <key>-v1.jpg, ... (build_weather_library.py)
ROTATE_S = 600             # show another variant of the current weather this often

# ---- the 10 x 5 key space --------------------------------------------------------------------------
CONDITION_GROUPS = [
    ("clear",         [0],                 "a clear, cloudless sky"),
    ("mostly_clear",  [1],                 "a mostly clear sky with a few thin wisps of cloud"),
    ("partly_cloudy", [2],                 "a partly cloudy sky, scattered soft cumulus clouds"),
    ("overcast",      [3],                 "a flat overcast grey sky, diffuse light"),
    ("fog",           [45, 48],            "dense fog, forms fading into mist"),
    ("drizzle",       [51, 53, 55, 56, 57], "fine drizzle, damp air, soft haze"),
    ("rain",          [61, 63, 66, 80, 81], "steady rain, rain streaks, wet reflective ground"),
    ("heavy_rain",    [65, 67, 82],        "a heavy downpour, sheets of rain, dark low clouds"),
    ("snow",          [71, 73, 75, 77, 85, 86], "falling snow, soft white flakes, everything muffled in white"),
    ("thunderstorm",  [95, 96, 99],        "a thunderstorm, towering dark clouds, distant lightning"),
]
CONDITION_LABEL = {"clear": "Clear", "mostly_clear": "Mostly clear", "partly_cloudy": "Partly cloudy", "overcast": "Overcast",
                   "fog": "Fog", "drizzle": "Drizzle", "rain": "Rain", "heavy_rain": "Heavy rain", "snow": "Snow", "thunderstorm": "Thunderstorm"}
TIMES = [
    ("dawn",   "first light of dawn, pale pink and blue"),
    ("morning", "soft morning light"),
    ("afternoon", "bright afternoon light"),
    ("golden", "golden hour, long warm light"),
    ("night",  "night, deep blue darkness with a faint glow"),
]
SCENES = [
    "a vast open landscape under a big sky",
    "a calm sea meeting the horizon",
    "rolling hills fading into the distance",
    "an endless open field with a lone tree",
    "a still lake reflecting the sky",
    "a wide valley seen from above",
]


def seed_library():
    """First run of a packaged install: copy the pictures that shipped in the bundle to the data dir."""
    if os.path.abspath(BUNDLED_LIBRARY) == os.path.abspath(LIBRARY_DIR) or not os.path.isdir(BUNDLED_LIBRARY):
        return
    import shutil
    copied = 0
    for name in os.listdir(BUNDLED_LIBRARY):
        dst = os.path.join(LIBRARY_DIR, name)
        if name.lower().endswith(".jpg") and not os.path.exists(dst):
            try:
                shutil.copyfile(os.path.join(BUNDLED_LIBRARY, name), dst)
                copied += 1
            except OSError:
                break
    if copied:
        print("weatherart: seeded %d pictures into %s" % (copied, LIBRARY_DIR))


def condition_group(code: int) -> str:
    for name, codes, _ in CONDITION_GROUPS:
        if code in codes:
            return name
    return "partly_cloudy"


def time_bucket(hour: int) -> str:
    return "dawn" if hour < 8 else "morning" if hour < 12 else "afternoon" if hour < 17 else "golden" if hour < 20 else "night"


def all_keys():
    return ["%s-%s" % (c[0], t[0]) for c in CONDITION_GROUPS for t in TIMES]


def build_prompt(key: str) -> str:
    cond, tod = key.split("-", 1)
    cdesc = next(c[2] for c in CONDITION_GROUPS if c[0] == cond)
    tdesc = next(t[1] for t in TIMES if t[0] == tod)
    return ("abstract atmospheric painting of %s, %s, %s, dreamlike, minimal, soft gradients, painterly, "
            "no buildings, no people, no text, no watermark, muted cinematic colours, wide composition"
            % (random.choice(SCENES), cdesc, tdesc))


def library_path(key: str, variant: int = 0) -> str:
    return os.path.join(LIBRARY_DIR, key + (".jpg" if variant == 0 else "-v%d.jpg" % variant))


def library_variants(key: str) -> list:
    """Indices of the variants that exist for a key."""
    return [v for v in range(VARIANTS) if os.path.exists(library_path(key, v))]


def key_for(code: int, hour: int) -> str:
    return "%s-%s" % (condition_group(code), time_bucket(hour))


class WeatherArt:
    def __init__(self, broadcast, comfy_base_getter):
        self.broadcast = broadcast
        self.comfy_base = comfy_base_getter      # () -> "http://127.0.0.1:8188" or None
        self.image_b64 = None
        self.key = None
        self.variant = 0
        self.changed_at = 0.0
        self.prompt = ""
        self.generated = 0
        self.weather = {}
        self.error = None
        self._regen = asyncio.Event()
        self._next = asyncio.Event()

    def snapshot(self, with_image=True) -> dict:
        d = {"key": self.key, "variant": self.variant, "prompt": self.prompt, "generated": self.generated, "error": self.error, **self.weather}
        d["image"] = self.image_b64 if with_image else None
        return d

    def request_regen(self):
        self._regen.set()

    def request_next(self):
        self._next.set()

    async def run(self):
        os.makedirs(LIBRARY_DIR, exist_ok=True)
        seed_library()
        while True:
            try:
                await self.step(force=self._regen.is_set(), rotate=self._next.is_set())
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:80]
                print("weatherart: error:", e)
            self._regen.clear(); self._next.clear()
            # re-check the forecast (and rotate the picture) every ROTATE_S, or sooner on regen / next
            waiter = asyncio.create_task(self._regen.wait()); nxt = asyncio.create_task(self._next.wait())
            done, _ = await asyncio.wait({waiter, nxt}, timeout=ROTATE_S, return_when=asyncio.FIRST_COMPLETED)
            for t in (waiter, nxt):
                if t not in done:
                    t.cancel()

    async def step(self, force=False, rotate=False):
        if not config.has_location():
            self.error = "set your location in config.json"
            await self.broadcast(self.snapshot(with_image=self.image_b64 is not None))
            return
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": LAT, "longitude": LON, "timezone": TZ,
                "current": "temperature_2m,weather_code", "daily": "weather_code,temperature_2m_max,temperature_2m_min", "forecast_days": 1,
            }, timeout=aiohttp.ClientTimeout(total=15)) as r:
                w = await r.json()
        cur = w["current"]; daily = w["daily"]
        code, temp = int(cur["weather_code"]), float(cur["temperature_2m"])
        group = condition_group(code)
        self.weather = {"temp": temp, "code": code, "condition": CONDITION_LABEL[group],
                        "hi": daily["temperature_2m_max"][0], "lo": daily["temperature_2m_min"][0]}
        key = key_for(code, datetime.now().hour)
        variants = library_variants(key)
        due = rotate or key != self.key or (time.time() - self.changed_at) >= ROTATE_S - 30
        if not force and self.image_b64 and key == self.key and not (due and len(variants) > 1):
            await self.broadcast(self.snapshot(with_image=False))
            return
        if not force and variants:
            others = [v for v in variants if v != self.variant] if key == self.key else variants
            v = random.choice(others or variants)
            self._load(library_path(key, v), key, "(library)", v)
            self.error = None
            await self.broadcast(self.snapshot())
            return
        path = library_path(key, self.variant if key == self.key else 0)
        base = self.comfy_base()
        if not base:
            self.error = "ComfyUI not running" if not os.path.exists(path) else None
            if os.path.exists(path):
                self._load(path, key, "(library)")
            await self.broadcast(self.snapshot(with_image=self.image_b64 is not None))
            return
        prompt = build_prompt(key)
        print("weatherart: rendering", key, "|", prompt[:80])
        jpg = await render(base, prompt)
        if jpg:
            with open(path, "wb") as f:
                f.write(jpg)
            self._load(path, key, prompt, self.variant if key == self.key else 0)
            self.error = None
            await self.broadcast(self.snapshot())

    def _load(self, path, key, prompt, variant=None):
        self.image_b64 = "data:image/jpeg;base64," + base64.b64encode(open(path, "rb").read()).decode()
        self.key, self.prompt, self.generated = key, prompt, time.time()
        self.variant = variant if variant is not None else (self.variant if key == self.key else 0)
        self.changed_at = time.time()


async def render(base: str, prompt: str) -> bytes | None:
    """Render one prompt through the saved workflow and return a square OUT_SIZE JPEG."""
    from PIL import Image
    tpl = json.load(open(TEMPLATE, encoding="utf-8"))
    tpl[PROMPT_NODE]["inputs"]["text"] = prompt
    for n in tpl.values():
        ins = n.get("inputs", {})
        for k in ("seed", "noise_seed"):
            if k in ins and isinstance(ins[k], int):
                ins[k] = random.randrange(0, 2**53)
    client_id = "rook-weather-" + uuid.uuid4().hex[:8]
    async with aiohttp.ClientSession() as s:
        async with s.post(base + "/prompt", json={"prompt": tpl, "client_id": client_id}, timeout=aiohttp.ClientTimeout(total=15)) as r:
            res = await r.json()
        pid = res.get("prompt_id")
        if not pid:
            print("weatherart: queue rejected:", str(res)[:200]); return None
        for _ in range(300):
            await asyncio.sleep(2)
            async with s.get(base + "/history/" + pid, timeout=aiohttp.ClientTimeout(total=10)) as r:
                h = await r.json()
            e = h.get(pid)
            if not e:
                continue
            if e.get("status", {}).get("status_str") == "error":
                print("weatherart: render failed"); return None
            imgs = [i for o in e.get("outputs", {}).values() for i in o.get("images", []) if i.get("type", "output") == "output"]
            if imgs:
                img = imgs[-1]
                async with s.get(base + "/view", params={"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": "output"},
                                 timeout=aiohttp.ClientTimeout(total=30)) as r:
                    data = await r.read()
                im = Image.open(io.BytesIO(data)).convert("RGB")
                w, h_ = im.size; side = min(w, h_)
                im = im.crop(((w - side) // 2, (h_ - side) // 2, (w - side) // 2 + side, (h_ - side) // 2 + side)).resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
                out = io.BytesIO(); im.save(out, "JPEG", quality=85)
                return out.getvalue()
    print("weatherart: timed out waiting for the render"); return None
