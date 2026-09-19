"""Queue the saved demo workflow (pc-agent/demo_prompt.json) in ComfyUI with a fresh random seed,
so it really renders (a repeated prompt would be served from ComfyUI's cache in ~1 s)."""
import json
import random
import urllib.request
import uuid

prompt = json.load(open("./pc-agent/demo_prompt.json"))
seeded = 0
for node in prompt.values():
    inputs = node.get("inputs", {})
    for key in ("seed", "noise_seed"):
        if key in inputs and isinstance(inputs[key], int):
            inputs[key] = random.randrange(0, 2**53)
            seeded += 1
    if "steps" in inputs and isinstance(inputs["steps"], int):
        inputs["steps"] = 40          # demo only: a longer, visible render (the saved copy normally runs 8 steps)
body = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode()
req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=body, headers={"Content-Type": "application/json"})
print("queued demo job:", json.load(urllib.request.urlopen(req)).get("prompt_id", "?")[:8], "| seeds randomised:", seeded)
