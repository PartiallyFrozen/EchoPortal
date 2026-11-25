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
| `latitude`, `longitude`, `timezone` | Used by the Clock and Sky faces for the forecast. Until set, those faces say so and stay quiet. |
| `comfy_urls` | Where to look for ComfyUI. The first one that answers wins. |
| `hub_port`, `media_port` | Reserved: the hub is fixed at 8765 and the HTTP media/API server at 8766 (the APK and the firewall rules expect those ports). Changing these keys has no effect. |
| `claude_hook_port` | 8767, the UDP port the Claude Code hooks report to. |
| `voice_backend`, `gemini_api_key`, `gemini_model` | Voice transcription. `auto` uses Gemini when a key is set, otherwise local Whisper. See `VOICE-GEMINI.md`. |
| `calendar_urls` | Read-only ICS/webcal URLs for the Agenda face. Empty means no calendar. |
| `calendar_days` | How many days ahead to expand (default 7). |
| `calendar_refresh_min` | How often to re-fetch the feeds (default 15 minutes). |
| `alerts` | Metric/service rules that publish `notify` banners. Empty means no alerts. |
| `wake_ramp_minutes` | Default length of the wake-up light when a wake alarm fires (default 20). |
| `media_clip` | File in `media/` that the Video Clock face loops (default `fireplace.mp4`). |
| `faces` | Server-side workers to run: `clock`, `videoclock`, `pcmon`, `voice`, `comfy`, `timer`, `calendar`, `sky`. Remove one to stop its polling; `markets` and `claude` are opt-in. An empty list runs everything. |
| `api_token` | Optional shared token for the WebSocket hub, the HTTP API and `/media/`. Empty means the LAN is trusted. |

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
| `timer` | on change | Timer/alarm snapshots, plus `{"op":"wake","minutes":N}` when a wake alarm fires |
| `calendar` | on change | The next `calendar_days` of events, refreshed from the ICS feeds |
| `notify` | on demand | Banners with an optional chime, published by `notify.py`, the HTTP API or an alert rule |

Clips are transcoded to 480 x 480 H.264 and served from `http://<pc>:8766/media/`, because the
Spot's decoder is picky and its download of a local file is far smoother than streaming.

## Timers and alarms

The PC owns the clock and persists timers to `timers.json`, so they survive a sleep, reboot or a
Wi-Fi drop. The Spot counts down locally from the snapshot; it does not need to poll. Voice
phrases such as *"set a timer for 10 minutes"*, *"timer 90 seconds"*, *"remind me in 5 minutes to
stretch"*, *"wake me at 6:30"* and *"cancel the timer"* are parsed by `timers.py` before the
transcript is treated as dictation. A wake alarm also broadcasts the wake message that starts the
shell's brightness ramp.

## HTTP API

The media server on port 8766 also exposes a small JSON API. All routes are open when `api_token`
is empty; otherwise send `Authorization: Bearer <token>`, `X-EchoPortal-Token: <token>`, or
`?token=<token>` (the Spot's media player can only use the query form). The same token guards the
WebSocket hub on 8765; a wrong one is closed with code 1008.

| Route | What it does |
| --- | --- |
| `GET /api/status` | Host, version, client count, voice backend, whether a token is required, the face list, the video clip name, timer/calendar summaries and the age of the last stats sample |
| `POST /notify` or `POST /api/notify` | `{"title","text","face","ttl","chime"}` banner |
| `GET /api/timers` | Current timer snapshot |
| `POST /api/timer` | `{"seconds"\|"minutes"\|"hours"\|"at","label","wake"}` |
| `POST /api/timer/cancel` | `{"id"\|"all"\|"dismiss"}` |
| `GET /api/calendar` | Calendar snapshot; add `?refresh=1` to re-fetch now |
| `POST /api/calendar/refresh` | Force a calendar refresh |

## Alerts

`alerts` is a list of rules evaluated with the telemetry stream. A metric rule fires when a
reading stays above `above` or below `below` for `for_s` seconds (for example CPU above 90% for a
minute); a service rule (`host` + `port`) fires when its TCP port stops answering. `name` and a
`text` template (with `{name}` and `{value}`) shape the banner, and `cooldown_s` limits repeats.
No rules means no banners.

## Discovery

The agent advertises `_echoportal._tcp.local.` with zeroconf, pointing at the hub port, so the
shell can find the PC on first run without a typed address. If zeroconf is missing or the network
blocks multicast, discovery is skipped and the agent still starts; use *PC agent address* on the
Spot instead. A stale advertisement from a previous run is handled by renaming the instance rather
than failing.

## Optional pieces

- **Voice** uses Gemini when `gemini_api_key` is set (see `VOICE-GEMINI.md`). Local Whisper is the
  fallback for source installs: it needs `requirements-voice.txt` and, for useful speed, an NVIDIA
  GPU, and downloads its model on first use. The packaged installer does **not** include Whisper,
  so voice needs a Gemini key there. Without either, every other face still works.
- **ComfyUI** powers the ComfyUI and Sky faces. Install the `rook_broadcast` node from
  `pc-agent/comfy_ext/rook_broadcast/` if you want progress for jobs you started from ComfyUI's own web UI,
  which otherwise only reports to the client that submitted them.
- **Claude Code hooks** are opt-in. Merge `pc-agent/claude_hooks.json` into your Claude Code
  settings, or run `python scripts/install_claude_hooks.py`. Each hook fires one UDP datagram at
  localhost and never blocks Claude Code.

## Security

The WebSocket hub, HTTP API and `/media/` all require the `api_token` shared with the Spot: an empty
value in the config is replaced with a generated token at startup (fail-closed if it cannot be
written). The voice face can type into the focused window. Read [SECURITY.md](../SECURITY.md) before
exposing the ports anywhere.
