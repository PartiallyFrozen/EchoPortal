# The agent

A small Python program that runs in the Windows system tray and feeds the Spot. Start it from the
installer's Start menu entry, or during development:

```bash
python pc-agent/tray.py     # tray icon, no console
python pc-agent/agent.py    # console with logs, for development
```

It writes `pc-agent/agent.log`. The tray menu has start and stop, a test banner, the log, the
ticker symbols, and "start with Windows".

## Why Windows only

The agent reads Windows performance counters through `win32pdh` for GPU engines, adapter memory,
physical disks and processor time, types into the focused window with `win32gui` and the clipboard,
and installs itself into the Startup folder. Those are the parts a Linux or macOS port would have
to replace; the sockets, polling and transcoding are portable. Pull requests welcome, but it is a
port, not a packaging flag.

## Settings

`config.json`, created next to the agent on first run from
[`packaging/config.example.json`](../packaging/config.example.json):

| Key | What it does |
| --- | --- |
| `latitude`, `longitude`, `timezone` | Used by the Sky face for the forecast. Until set, that face says so and stays quiet. |
| `comfy_urls` | Where to look for ComfyUI. The first one that answers wins. |
| `hub_port`, `media_port` | 8765 for the WebSocket hub, 8766 for the HTTP media server. |
| `claude_hook_port` | 8767, the UDP port the Claude Code hooks report to. |

Ticker symbols live in `pc-agent/tickers.json`, edited from the tray while it runs.

## Channels

The agent broadcasts JSON to every connected device as `{"ch": <channel>, "data": {...}}`. Faces
subscribe by declaring the channels they want. A snapshot of each channel is sent when a device
connects, so a face is never blank while it waits for the next tick.

| Channel | Rate | Carries |
| --- | --- | --- |
| `stats` | 1 s | CPU, memory, disks, network, GPU utilisation, VRAM, temperature, power, top processes |
| `comfy` | on change | Queue state, current node, step progress, elapsed and ETA, the latest image or clip |
| `ticker` | streaming | Crypto from Coinbase's WebSocket, stocks polled from Yahoo, FX conversion |
| `weatherart` | 10 min | The forecast and the matching picture from the pre-rendered library |
| `claude` | 2 s | What Claude Code is doing, from its hooks |
| `notify` | on demand | Banners with an optional chime, published by `notify.py` or any client |

Clips are transcoded to 480 x 480 H.264 and served from `http://<pc>:8766/media/`, because the
Spot's decoder is picky and its download of a local file is far smoother than streaming.

## Optional pieces

- **Voice** needs `requirements-voice.txt` and, for useful speed, an NVIDIA GPU. The model
  downloads on first use. Without it, every other face still works.
- **ComfyUI** powers the ComfyUI and Sky faces. Install the `rook_broadcast` node from
  `pc-agent/comfy_ext/rook_broadcast/` if you want progress for jobs you started from ComfyUI's own web UI,
  which otherwise only reports to the client that submitted them.
- **Claude Code hooks** are opt-in. Merge `pc-agent/claude_hooks.json` into your Claude Code
  settings, or run `python scripts/install_claude_hooks.py`. Each hook fires one UDP datagram at
  localhost and never blocks Claude Code.

## Security

No authentication, by design, on a LAN you trust. Read [SECURITY.md](../SECURITY.md) before
exposing the ports anywhere.
