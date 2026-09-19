"""Render the 50-image weather library (10 conditions x 5 times of day) through ComfyUI. Resumable: skips existing files.

    python scripts/build_weather_library.py              # render every missing key/variant (weatherart.VARIANTS per key)
    python scripts/build_weather_library.py --redo KEY   # re-render variant 0 of one key (e.g. rain-night), or KEY:2 for variant 2
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pc-agent"))
import aiohttp  # noqa: E402
import weatherart  # noqa: E402


async def comfy_base():
    for base in ("http://127.0.0.1:8188", "http://127.0.0.1:8000"):
        try:
            async with aiohttp.ClientSession() as s, s.get(base + "/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status == 200:
                    return base
        except Exception:  # noqa: BLE001
            pass
    return None


async def main():
    os.makedirs(weatherart.LIBRARY_DIR, exist_ok=True)
    redo = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--redo" else None
    if redo:
        k, _, v = redo.partition(":")
        todo = [(k, int(v or 0))]
    else:   # variant-major order so a partial run still spreads new pictures across every key
        todo = [(k, v) for v in range(weatherart.VARIANTS) for k in weatherart.all_keys() if not os.path.exists(weatherart.library_path(k, v))]
    print("library: %d keys x %d variants, %d to render" % (len(weatherart.all_keys()), weatherart.VARIANTS, len(todo)))
    base = await comfy_base()
    if not base:
        print("ComfyUI is not running"); return
    t_all = time.time()
    for i, (key, v) in enumerate(todo, 1):
        prompt = weatherart.build_prompt(key)
        t0 = time.time()
        jpg = await weatherart.render(base, prompt)
        name = key + ("" if v == 0 else ":%d" % v)
        if jpg:
            with open(weatherart.library_path(key, v), "wb") as f:
                f.write(jpg)
            print("[%3d/%d] %-28s %5.1fs  %3d KB  %s" % (i, len(todo), name, time.time() - t0, len(jpg) // 1024, prompt[:60]), flush=True)
        else:
            print("[%3d/%d] %-28s FAILED" % (i, len(todo), name), flush=True)
    print("done in %.0fs" % (time.time() - t_all))


asyncio.run(main())
