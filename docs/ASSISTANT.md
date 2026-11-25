# Jarvis assistant

Jarvis is a reasoning layer **inside EchoPortal**, not a second app. The PC already owns the mic,
the hub, timers, calendars and the Windows desktop; this adds a model + tool loop, persistent memory
and a Spot face that shows and speaks the answer.

```
Spot: "Jarvis" wake word (or Voice face target = Jarvis)
   → audio over ws :8765 → voice.py → transcript
   → assistant.py → Gemini / local Ollama → tool calls → short reply
   → assistant channel :8765 → AssistantFace + Piper/SAPI WAV over :8766/media/tts/
```

## Before you use it

1. **Confirm `api_token` in the PC's `config.json` is non-empty** and enter the same value on the
   Spot (long-press → **PC hub token…**). An empty value is generated at startup and a read-only
   config stops startup; the hub can type into your focused Windows window. See
   [SECURITY.md](../SECURITY.md).
2. Set `gemini_api_key` (or `GEMINI_API_KEY`) for the default hybrid brain, **and/or** run a local
   Ollama-compatible server for offline use. This machine's active local model is `qwen3:14b`;
   `qwen2.5:7b-instruct` is also installed and appears in the picker. Optional extras:
   `ollama pull qwen2.5vl:7b` for offline screenshot vision and `ollama pull nomic-embed-text` for
   semantic note search. The RX 7700 (16 GB VRAM, ROCm `gfx1101`) is environment context only —
   there is no VRAM setting.
3. No extra Python package is needed for Piper speech, vision, RAG, web search or Home Assistant.
   `paho-mqtt>=2.0` is only needed if you later configure the robot tool; robot control stays
   disabled until its MQTT topic and payload shape are supplied.
4. Restart the agent. If your existing `config.json` already has a `faces` list, add `"assistant"`
   to it; the installer does not overwrite an existing config. The APK is installed when the target
   rook device arrives.

## Talking to it

- Say **"Jarvis"** (Porcupine wake word) then your request. The shell opens the Voice face with
  Jarvis selected; stop speaking and the transcript goes to the assistant.
- Or open the Voice face and tap the target pill until it reads **→ Jarvis**. The target resets
  after one turn.
- Any assistant activity switches the Spot to the **Jarvis** face. A reply is shown on the round
  screen and played aloud; tap the answer to replay it. If the Spot is asleep, replies arrive as
  a banner instead.
- Long replies stream onto the face as the model writes them. Complete sentences are spoken while
  the rest is still streaming; a `[pause]` marker adds a spoken beat but is hidden on screen. Send
  **cancel** (or press the Stop button) to abort an in-flight turn.
- Tools that change something (typing, opening apps, window/media control, Home Assistant, memory
  deletion, calendar/task writes, robot movement) show a **Yes / No / Always** confirmation. Nothing
  runs until you approve it. **Always** persists an exact tool+argument signature in
  `assistant_auto_allow`, so only that specific action stops prompting. Confirmations expire after
  `assistant_confirm_timeout` seconds.

From a script or terminal (token required if set):

```bat
curl -X POST http://<pc-ip>:8766/api/assistant ^
  -H "Content-Type: application/json" -H "X-EchoPortal-Token: <token>" ^
  -d "{\"text\":\"what is on my calendar?\"}"
```

## Configuration

All keys live in `config.json` next to the agent (`Start → Agent settings`). New keys are added with
defaults on first run; an existing config is not overwritten.

| Key | Default | Meaning |
| --- | --- | --- |
| `assistant_backend` | `hybrid` | `gemini` (needs key), `local`, or `hybrid` (Gemini first, local fallback). |
| `assistant_model_choice` | `hybrid` | Dashboard picker: `hybrid`, `gemini:<model>` or `local:<model>`; an explicit choice beats topic routing (except vision). |
| `assistant_gemini_model` | `gemini-flash-lite-latest` | `-latest` alias survives model retirements. |
| `assistant_local_url` | `http://127.0.0.1:11434` | Ollama-compatible base URL; `/api/chat` is appended if missing. |
| `assistant_local_model` | `qwen3:14b` | Active local model; `qwen2.5:7b-instruct` is also installed and listed in the picker. |
| `assistant_keep_alive` | `10m` | Ollama keep-alive between turns; invalid values fall back to `10m`. |
| `assistant_speak` | `true` | Also write WAVs and play them on the Spot. |
| `assistant_stream_tts` | `true` | Speak each completed sentence while the rest of the answer streams. |
| `assistant_pause_marker` | `[pause]` | Spoken pause; stripped from visible text and speech. |
| `tts_backend` | `auto` | `auto` (Piper if installed, else SAPI), `piper`, or `sapi`. |
| `tts_piper_model` | `en_GB-alan-medium.onnx` | Bundled offline British male voice (with matching `.onnx.json`). |
| `tts_piper_length_scale` | `0.95` | Pace: below 1 faster, above 1 slower. |
| `tts_style` | `jarvis` | `jarvis` (measured phrasing, en-GB male SAPI preference) or `natural`. |
| `tts_voice` | `""` | Exact SAPI voice name; blank picks the best installed match for the style. |
| `tts_rate`, `tts_pitch`, `tts_volume` | `null` | SAPI overrides (`-10..10`, `-8..8`, `0..100`); `null` uses the style preset (0, 0, 100). |
| `assistant_confirm_timeout` | `120` | Seconds before an unanswered approval is discarded. |
| `assistant_auto_allow` | `{}` | Tool+argument signatures approved with **Always**. |
| `assistant_knowledge_dirs` | `[]` | Absolute folders searched by `search_notes`. Empty falls back to `<agent>/notes` if present. |
| `assistant_apps` | `{}` | Name → executable allowlist for `open_app` (example config includes Notepad). |
| `assistant_commands` | `[]` | Exact command strings allowed for `run_command`. |
| `assistant_embed_model` | `nomic-embed-text` | Ollama embeddings for semantic note ranking; keywords remain the fallback. |
| `assistant_memory_half_life_days` | `30` | Age scale for recall decay; older facts rank lower (down to half weight). |
| `assistant_memory_compact_after` | `40` | Summarise older conversation turns beyond this count; `0` disables compaction. |
| `assistant_rag_enabled` | `true` | Use embeddings in `search_notes` when Ollama answers; `false` uses keywords only. |
| `assistant_tool_subset_size` | `8` | Tool declarations sent per turn; `0` sends every tool. |
| `assistant_verify_complex` | `true` | One quiet review pass for longer/complex answers. |
| `assistant_plugin_dir` | `""` | Python plugin folder; blank uses `<agent>/plugins`. |
| `assistant_vision_model` | `qwen2.5vl:7b` | Multimodal model for `look_at_screen`; a blank legacy value still resolves to this default. If Gemini fails, the local model answers instead. |
| `assistant_vision_backend` | `auto` | `gemini`, `local` or `auto` (local Ollama when no Gemini key). |
| `assistant_route_private` | `local` | Backend for notes/memory/calendar turns (`auto` keeps the chosen backend). |
| `assistant_route_web` | `gemini` | Backend for web turns. |
| `assistant_route_vision` | `auto` | Backend after a screenshot tool result; `auto` keeps the chosen backend. |
| `assistant_private_keywords` | note/notes/password/… | Words that mark a question as private for routing. |
| `assistant_history_limit` | `50` | Rows shown by the dashboard history table. |
| `assistant_metrics_limit` | `200` | In-memory model-call samples retained. |
| `assistant_devices` | `{}` | Per-Spot model/voice preferences keyed by device UUID; see below. |
| `search_provider` | `auto` | `auto`, `duckduckgo`, `searxng`, `brave`, `serper`, `tavily`. |
| `search_searxng_url` | `""` | Base URL of a SearXNG instance. |
| `search_allow_private` | `false` | Allow `web_fetch` to reach loopback/private hosts (SSRF guard off). |
| `home_assistant_url`, `home_assistant_token` | `""` | REST API base URL and long-lived token. |
| `home_assistant_services` | light/switch/scene/climate | Allowlist for `ha_call`; fail-closed. |
| `calendar_urls` | `[]` | Read-only ICS feeds. Local absolute paths work like URLs; use them when calendar data must stay on the PC. |
| `calendar_caldav_url`, `calendar_caldav_user`, `calendar_caldav_password` | `""` | Optional CalDAV collection for `calendar_add`. |
| `robot_enabled`, `robot_broker`, … | disabled/empty | MQTT transport for the robot tool; control stays disabled until configured. |
| `faces` | includes `assistant` | Remove `assistant` to disable the worker entirely. |

A per-device entry overrides the global picker and TTS keys for that Spot only:

```json
"assistant_devices": {
  "00000000-0000-0000-0000-000000000000": {
    "name": "Kitchen Spot",
    "model_choice": "local:qwen3:14b",
    "tts_style": "natural",
    "tts_length_scale": 0.95,
    "tts_voice": "",
    "tts_rate": 0,
    "tts_pitch": 0,
    "tts_volume": 100
  }
}
```

Swap in `local:qwen2.5:7b-instruct` for a smaller device model. Supported keys are `name`, `label`,
`model_choice`, `tts_length_scale`, `tts_style`, `tts_voice`, `tts_rate`, `tts_pitch` and
`tts_volume`. **Per-device settings win:** `model_choice` overrides the
global `assistant_model_choice`, and each TTS key overrides its global counterpart for that device;
unknown keys are ignored and values are clamped to the same safe ranges. `name`/`label` are display
only, including the wording prompt. Screenshot *tool calls* are the exception: they use the
global `assistant_vision_backend`/`assistant_vision_model`, and the follow-up route follows
`assistant_route_vision`. The dashboard **Settings** panel writes these via
`POST /api/assistant/device`.

`GEMINI_API_KEY` overrides `gemini_api_key`. `BRAVE_API_KEY`, `SERPER_API_KEY` and
`TAVILY_API_KEY` override the matching web-search settings. Allowlists are fail-closed: an app,
command or Home Assistant service that is not listed is refused, and the refusal is reported back
to the model.

### Local fallback (Ollama)

The hybrid backend works with Gemini alone; the local model is the fallback and the default home for
private turns. This machine is set up as follows:

- Ollama installed via `winget install Ollama.Ollama`, running on `http://127.0.0.1:11434`.
- Model: `qwen3:14b` (`ollama pull qwen3:14b`); `qwen2.5:7b-instruct` is still installed as a
  smaller alternative and appears in the dashboard picker.
- Ollama detects the RX 7700 through ROCm (`gfx1101`, 16 GB VRAM), so inference is GPU-accelerated.
  That hardware is context only; no config key depends on it.
- Jarvis sends `think: false` and `keep_alive` (`assistant_keep_alive`, default `10m`), so
  thinking-capable models answer quickly and stay loaded between turns without leaking reasoning.
- Optional offline add-ons: `ollama pull qwen2.5vl:7b` for `look_at_screen` and
  `ollama pull nomic-embed-text` for semantic note ranking.
- `assistant_backend = "hybrid"` and the local URL/model are already the defaults.

Check the service with `curl http://127.0.0.1:11434/api/tags`. The picker calls `/api/tags` too, so
every installed Ollama model (including `qwen2.5:7b-instruct`) appears as a `local:` option.

The model only sees the tools relevant to each question. Clear intents also get a short turn-level
hint such as "call `calendar`", and earlier assistant replies are marked unverified so a stale
"I can't check that" answer cannot override a tool that is available now. If the model skips a
required tool, Jarvis retries once with a stronger system hint; if it prints a tool call as raw
JSON, that JSON is executed instead of shown to the user. A read-only tool that is still skipped is
called directly with the question as its query; an explicit "Remember that …" request is stored the
same way. VRAM and Sabres answers use the tool's authoritative value so the model cannot drop the
number or game details. Unconfigured capabilities (the robot
vacuum until MQTT details are supplied) are removed from the tool list and answered with a plain
refusal rather than a confirmation card.

## Tools

| Tool | What it does | Confirmation |
| --- | --- | --- |
| `search_notes` | Keyword search over `assistant_knowledge_dirs`, re-ranked with local embeddings when available. | no |
| `web_search` | Provider-backed search; DuckDuckGo fallback needs no API key. | no |
| `web_fetch` | Fetch a public page; private hosts are blocked unless allowed. | no |
| `remember`, `recall`, `forget` | Persistent SQLite facts and conversation turns. | `forget`: **yes** |
| `look_at_screen` | Capture the desktop and ask Gemini or offline `qwen2.5vl:7b` about it. | **yes** |
| `save_draft` | Writes a Markdown file to `assistant_drafts/`. | no |
| `task_add`, `task_list` | Append/read the local `assistant_tasks.md`. | `task_add`: **yes** |
| `timer`, `calendar` | Existing timer store and read-only ICS feeds (local files need no network). | no |
| `sabres_schedule` | Local Buffalo Sabres 2026-27 lookup: date, time, venue, opponent, TV and jersey. | no |
| `calendar_add` | PUT one VEVENT to the configured CalDAV collection. | **yes** |
| `pc_stats` | CPU/RAM/GPU/disk sample; takes one on demand if the Spot sampler is idle. | no |
| `window_list`, `window_focus`, `window_close` | Windows window control. | focus/close: **yes** |
| `media_control` | Play/pause, next, previous, volume up/down, mute. | **yes** |
| `ha_state`, `ha_call` | Home Assistant states and allowlisted services. | `ha_call`: **yes** |
| `notify` | Shows a banner on the Spot. | no |
| `type_text` | Pastes into the focused window. | **yes** |
| `open_app`, `run_command` | Allowlisted executable / exact command. | **yes** |
| `undo` | Reverse the last reversible local action for this device (see below). | no |
| `robot` | Transport scaffold for `start`, `stop`, `home`/`dock`, `find`; disabled until MQTT details are supplied. | **yes** |

Only tools relevant to the question are declared (`assistant_tool_subset_size`), plus any tool
already used this turn; `0` sends all of them. The model cannot import Python or reach the hub
directly. Tool results are truncated before they go back to the model. Invalid tool calls are
reported back once for a corrected retry, then answered with a short refusal.

### Local calendars and the Sabres schedule

`calendar_urls` accepts local `.ics` paths as well as HTTP(S) URLs. The active install keeps five
calendar snapshots under `<knowledge dir>/calendars/`; no network request is needed to read them.
They are snapshots, so changes made in Google Calendar appear only after the `.ics` files are
downloaded again. The old config, including the original secret URLs, is backed up as
`config.json.bak-localcal-*` next to `config.json`.

The Buffalo Sabres 2026-27 schedule is stored in the knowledge folder as
`Buffalo-Sabres-2026-27-Schedule.json` (structured), `.md` (searchable prose) and
`calendars/Buffalo-Sabres-2026-27.ics` (full season archive). `tools/build_sabres_schedule.py`
regenerates all three from the NHL API plus the team's published jersey schedule. Jarvis answers
schedule and jersey questions with the `sabres_schedule` tool, so no web lookup is required. The
local NHL calendar feed is enriched with the jersey name in each Sabres event title and is the feed
the `calendar` tool reads; the standalone Sabres ICS is an archive, not a second active feed.
Sabres-only replies use the tool's authoritative one-line summary so the date/time, teams, venue,
TV/status and jersey are never dropped by the model. If Ollama's runner gets stuck and aborts a
prediction for repeated tokens, Jarvis unloads that model, retries once and reports no error to the
user.

## Multi-device

Each Spot gets a persistent UUID (stored in the app's settings) and a friendly name. The UUID is
appended to the hub URL as `?device=<uuid>` and attached to `ask`/`confirm` payloads. The PC records
last-seen info per id in `assistant_devices`; a friendly name can be set under
`assistant_devices.<uuid>.name` or from the Spot's long-press **Device name…** menu (display only).
Each entry can also carry `model_choice` and TTS overrides; those win over the dashboard's global
picker and the global `tts_*` keys for that one Spot (see the table above). Replies are sent to the
requesting Spot directly and relayed to other connected Spots. `GET /api/assistant/devices` lists
known ids and last-seen times; **Devices** in the inspector shows the same data.

## The Jarvis interface (PC)

`http://<pc-ip>:8766/dashboard?token=<api_token>` (or `/ui`) is the main interface. It is a real chat
app, not a status dump: a conversation sidebar, streaming answers with a typing caret, tool traces,
inline Yes / No / Always approval cards, streaming spoken replies and a slide-out inspector.
Send with Enter, Shift+Enter for a new line. **New conversation** clears the transcript; the sidebar
keeps recent turns from the assistant's persistent store. The inspector's workspace buttons are
**Devices** (last-seen Spots), **Settings** (this device's model and voice), **Memory** (facts and
chat turns) and **Observability**.

**Observability** shows backend, model, readiness, fallbacks, successful/failed tool calls and
pending turns, plus the last 20 model calls from `GET /api/assistant/metrics` (time, latency, answer
length and tools ok/total). **Settings** edits `assistant_devices` for the chosen device id: model,
style, speech pace (`tts_length_scale`), SAPI voice, rate and pitch; saving posts to
`/api/assistant/device`.

The model picker in the top bar chooses Hybrid, a Gemini model, or any locally installed Ollama
model; it persists in `assistant_model_choice`. A per-device `model_choice` still wins for that Spot.
Each recent chat has a **×** button, and the bin in the top bar deletes all saved chats. Both ask
for confirmation and leave remembered facts intact.

The interface is driven entirely by the same WebSocket hub the Spots use (`ws://<pc>:8765`), so
there is no second backend. A turn can be aborted with `POST /api/assistant/cancel`
(`{"token":"..."}`; no token cancels all) or the Stop button.

### Customizing it

Everything visual lives next to `config.json` in `%LOCALAPPDATA%\Programs\EchoPortal\ui\`:

```
ui/theme.css            loaded after the built-in stylesheet; any CSS or :root variable wins
ui/plugins/*.js         plain scripts, discovered on each page load
ui/plugins/blob.js      example: an animated blob that breathes on tool calls and pulses with audio
ui/plugins/README.txt   the plugin API reference
```

A plugin registers hooks and receives events; no rebuild or agent restart is needed — just refresh
the browser:

```js
Jarvis.plugin({
  name: 'my-plugin',
  init: function (ui) { /* ui.hero(), ui.thread(), ui.body() */ },
  onPrompt: function (text) {},
  onThinking: function () {},
  onTool: function (name) {},
  onStream: function (chunk, wholeText) {},
  onConfirm: function (data) {},
  onReply: function (data) {},
  onAudio: function (audioElement) {},
  onStatus: function (status) {},
  onError: function (data) {},
  onCancelled: function () {},
  onFallback: function (data) {},
  onVerified: function (data) {},
  onPause: function (data) {},
  onToolError: function (data) {},
  onSpeak: function (text) {}
});
```

`onAudio` fires once per spoken sentence clip, so visualisations keep reacting during a long reply;
it normally receives an `Audio` element when playback starts, and the raw media path from the
`audio` websocket event. `onVerified` marks a completed review pass, `onFallback` a local fallback,
`onPause` a pause marker and `onToolError` a rejected tool call. A `speak` event also exists; use
`Jarvis.on('speak', fn)` to subscribe to it. The shipped blob uses `onAudio` with the Web Audio API to modulate its radius from
the spoken WAV's amplitude. Delete `blob.js` to remove it, add any simple `.js` file to build your
own, or replace `theme.css` entirely; only safe filenames are loaded.

### Python plugin SDK

Server-side plugins are optional `.py` files in `<agent>/plugins/` (or `assistant_plugin_dir`).
Each file defines `register(api)` and can subscribe to the same event names or add a read-only
callable; one broken plugin never stops the agent. No hub or config internals are passed in.

```python
def register(api):
    api.on('tool', lambda event: print('tool:', event.get('text')))
    api.emit('plugin', note='hello')  # progress event to connected clients
    api.register_tool('my_helper', lambda text='': {'ok': True, 'text': text},
                      'Optional helper', {'text': 'string'})
```

`set_model_choice`, device preferences and approvals are not part of this API. Plugin helpers are
excluded from the confirmation flow on purpose: they should stay self-contained/read-only. A
registered helper is advertised in the model's tool schema, validated against its declared
parameters and callable by the model. `api.emit()` schedules progress events on the hub loop, so
connected clients see them while the agent is running.

### MCP servers (community tools)

`mcp_servers` in `config.json` points Jarvis at any Model Context Protocol server that speaks
Streamable HTTP. Each discovered tool is registered through the same SDK surface as a Python plugin
and appears as `mcp_<server>_<tool>` in the model's tool list:

```json
"mcp_servers": {
  "files": {"url": "http://127.0.0.1:8000/mcp", "token": "", "tools": []}
}
```

`tools` is an optional allowlist; leave it empty to expose everything. `mcp_timeout` (seconds,
default 10) bounds discovery. An offline or broken server is skipped with a log line — the agent
still starts. MCP tools are read-only by design and are not part of the confirm allowlist.

### Home Assistant, both directions

The existing `ha_state`/`ha_call` tools need `home_assistant_url` and a long-lived token. Once set,
a compact home snapshot (lights, switches, climate, locks) is refreshed every minute and added to
the system prompt, so "is the front door locked?" is answered from live state without a tool round.

For the reverse direction, copy `custom_components/jarvis_conversation/` into your HA config and add
**Jarvis Conversation** as a conversation agent. Voice assistants and automations then reach the
same brain through `/v1/chat/completions`. See the README in that folder.

### Feedback, briefings and barge-in

- **Memory feedback:** the Memory panel lists remembered facts with ▲/▼ buttons. Votes are stored
  on the fact and add up to ±2.0 to its recall rank, so useful facts surface first. The
  `/api/assistant/facts` and `/api/assistant/feedback` routes are also available to scripts.
- **Proactive briefings:** set `briefing_enabled: true` and optionally change
  `briefing_morning_time` / `briefing_evening_time` (local `HH:MM`). A banner and, with
  `briefing_speak`, a spoken summary of calendar events and live home state are pushed to all clients.
  Disabled by default; `/api/status` reports the next scheduled time under `briefing`.
- **Spot barge-in:** the Kotlin source now exposes `AssistantFace.resetAudio()` and a
  `VoiceFace.onVoiceDetected` rising-edge hook, wired in `MainActivity` so speaking during a reply
  stops and flushes queued sentence audio. Source only — no APK is built or installed here.

### Open WebUI

For a full-featured hosted chat frontend, the agent also exposes an OpenAI-compatible API:

- `GET /v1/models` → one model, `jarvis`
- `POST /v1/chat/completions` → blocking or SSE streaming, mapped to `AssistantEngine.ask()`

In Open WebUI, add an **OpenAI-compatible** connection with base URL `http://<pc-ip>:8766/v1` and
the agent's `api_token` as the key, then select the `jarvis` model. Tool approvals cannot be rendered
as native buttons there, so a pending confirmation is returned as text — reply `YES`, `NO` or
`ALWAYS` and the next request resolves it. The Jarvis UI remains the place for full tool control and
Spot management; Open WebUI is an alternative conversation surface over the same brain.

## Spoken replies

`tts.py` renders replies to a WAV under `media/tts/`; the media server already serves that folder, so
`AssistantFace` only needs `http://<pc>:8766/media/tts/<file>?token=<api_token>`. Old files are cleaned
up after an hour (at most 40 files). Confirmation prompts are spoken too. If synthesis fails, the
text still appears.

### Streaming speech

With `assistant_speak` and `assistant_stream_tts` both true (the defaults), every completed sentence
emits a `speak` event; the agent synthesises that sentence at once and broadcasts an `audio` event
with `segment: true`. The finished reply includes `audio_segments` and `streamed_audio: true`, so the
Spot plays clips as they arrive instead of waiting for the whole answer. With streaming off, one WAV
is rendered at the end. Per-device TTS settings apply to every clip. `assistant_pause_marker`
(`[pause]`) is stripped from both the shown text and the spoken audio, with a `pause` event for the
UI. The request's `device` id flows through to TTS, so a Spot's overrides change its own voice.

### The voice

The default backend is **Piper**, a small standalone neural TTS runtime bundled in `piper/` beside
the agent. It uses `en_GB-alan-medium`, a clear British male voice that runs entirely offline — no
API key, no internet at reply time, no admin rights. Windows SAPI remains as a fallback. Several
other voices are bundled (`en_US-lessac-medium`, `en_GB-northern_english_male-medium`). This is an
original voice and manner, not a copy of any actor's voice.

```json
"tts_backend": "auto",
"tts_piper_model": "en_GB-alan-medium.onnx",
"tts_piper_length_scale": 0.95,
"tts_style": "jarvis"
```

- **`tts_backend`** — `auto` (Piper if installed, else SAPI), `piper`, or `sapi`.
- **`tts_piper_model`** — an `.onnx` filename inside `piper/`. Other Piper voices can be dropped in
  from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices); keep the matching
  `.onnx.json` file beside it. Unsafe names are ignored.
- **`tts_piper_length_scale`** — speech pace: `1.0` natural, above 1 slower, below 1 faster. Leave it
  near 1 — large values sound dragged out.
- **`tts_style`** — `jarvis` (default) adds calm, understated phrasing to the system prompt and
  prefers en-GB male SAPI voices; `natural` uses the plain prompt. It does not change the Piper voice.

SAPI-only overrides, used when Piper is unavailable:

```json
"tts_voice": "", "tts_rate": null, "tts_pitch": null, "tts_volume": null
```

`tts_voice` is an exact SAPI voice name (blank picks en-GB male, then en-GB, then any male). `tts_rate`
is `-10..10` and `tts_pitch` is `-8..8` semitones; `tts_volume` is `0..100`. `null` uses the style
preset (0, 0, 100) because SAPI's steps are coarse and WAV pitch resampling adds artifacts. Any of
these can also be set per device, where the device value wins. Install `Microsoft George` or `Ryan`
via Windows Settings → Time & language → Speech if you want a better SAPI fallback voice.

## Robot vacuum status

`robot.py` is a **transport scaffold**, not a finished device integration. The ZOOZEE PWA in the
workspace shows the Slamtec command IDs (`start/sweep = 26`, `stop = 24`, `home/dock = 25`,
`find = 56`) and the cloud MQTT endpoint, but the topic and payload shape are device-specific.
Until `robot_enabled`, `robot_broker` and `robot_topic` are filled in, the tool returns
"robot is not configured" instead of pretending to act.

## Verification, retry and undo

- **Two-stage verification:** with `assistant_verify_complex` true, long questions, "compare / plan /
  why / explain / summarise" prompts and turns with two or more tool results get one extra review
  call with no tools. If the review returns a non-empty answer without tool calls, it replaces the
  draft and emits `verified`; failure keeps the original. It is a consistency pass, not a guarantee.
- **Validation and retry:** each model tool call is matched against known tools and the callable's
  signature (unknown arguments are rejected, simple types coerced). A bad call emits `tool_error`
  and the model gets one prompted retry; after that Jarvis reports that the tool could not be used
  safely.
- **Undo:** the `undo` tool reverses only the most recent reversible *local* action for the
  requesting device — `remember`, `forget`, `save_draft`, `task_add` or `calendar_add`. The stack is
  in memory, capped at 20 entries per device, and is lost when the agent restarts. It cannot
  reverse typing, window/media actions, timers, web calls, Home Assistant services or robot
  commands; an unsupported inverse is reported plainly.

## HTTP API

All routes live on port 8766 and use the shared token where configured: `X-EchoPortal-Token`,
`Authorization: Bearer <token>` or `?token=`; a bad token gets 401. History and metrics reads
accept `?limit=` (1–500).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/assistant` | `{"text":"…","device":"…","dry_run":false}`; `dry_run: true` previews state-changing tools without executing them (confirmation cards still appear). |
| `GET` | `/api/assistant/models` | Picker `choices` and `current` id; local choices come from Ollama `/api/tags`. |
| `POST` | `/api/assistant/model` | `{"id":"local:qwen3:14b"}` persists the global picker. |
| `GET`, `POST` | `/api/assistant/device` | Read or merge per-device `values` (`?device=` or body `device`); per-device model/voice win. |
| `GET` | `/api/assistant/devices` | Known device ids, friendly names and last-seen times. |
| `GET` | `/api/assistant/metrics` | Recent model calls: backend, model, ms, chars, tools, tools_ok, tools_failed, fallback, device. |
| `GET`, `DELETE` | `/api/assistant/history` | Recent saved turns / delete all (remembered facts stay). |
| `DELETE` | `/api/assistant/history/{id}` | Delete one saved turn. |
| `GET` | `/api/assistant/facts` | Remembered facts ordered by feedback weight; shown in the Memory panel. |
| `POST` | `/api/assistant/feedback` | `{"fact_id":12,"vote":"up"\|"down"}` changes a fact's recall weight. |
| `POST` | `/api/assistant/cancel` | `{"token":"…"}`; no token cancels all turns. |
| `GET` | `/api/status` | Agent health, ports, connected clients, backend/model and device count. |
| `GET` | `/dashboard`, `/ui` | Jarvis chat UI (token entered in the page). |
| `GET` | `/ui/theme.css`, `/ui/plugins/{name}` | User CSS and discovered JS plugins. |
| `GET`, `POST` | `/v1/models`, `/v1/chat/completions` | OpenAI-compatible surface used by Open WebUI. |

## Regression evals

`evals/run_eval.py` replays `evals/cases.json` (general, memory, timer, web, notes, calendar, pc,
desktop, home, vision, sports and safety prompts) against one or more picker ids and writes
`evals/last_report.json`:

```bat
python evals/run_eval.py --model local:qwen3:14b
python evals/run_eval.py --model hybrid --model local:qwen2.5:7b-instruct --category timer --limit 10
python evals/run_eval.py --no-dry-run --output evals\custom_report.json
```

The agent must be running. Defaults: base `http://127.0.0.1:8766`, model `local:qwen3:14b`, token
read from `C:\Users\Justin\AppData\Local\Temp\opencode\echoportal_token.txt` (`--token-file`
overrides). **Dry run is the default** — state-changing tools are previewed, not executed
including `undo`; `--no-dry-run` really executes them. The runner reads the current picker,
switches models per run and restores the original in a `finally` block even on failure. Cases are
never imported by pytest; unit tests stay offline. A case's `keywords` are acceptable alternatives
(any-of); `all_keywords` requires every term. Cases marked `dry_run_knowledge` depend on an earlier
`remember` that dry run does not persist: their tool routing is still scored, but the answer keyword
check is skipped and reported as `dry_run_limitations` instead of a failure. Use `--no-dry-run` on a
disposable install if those answers must be checked end to end. `python evals/check_cases.py`
validates the whole set offline, and `--category <name>` or `--list` filters by category. CI runs the
schema check on every change.

## Wire format

New channel `assistant`; every payload is `{"ch": "assistant", "data": {...}}`. Most messages carry
`device` (the Spot UUID) so multiple Spots can be told apart.

| Op | Direction | Data |
| --- | --- | --- |
| `status` | PC → Spot | `backend`, `model`, `model_choice`, `ready`, `tools`, `pending`, `metrics`, `fallbacks`, tool ok/fail counts, `memory`. |
| `prompt`, `thinking`, `tool` | PC → Spot | `text`, `token`. |
| `stream` | PC → Spot | Partial answer `text`, `token` (throttled to ~10/s). |
| `speak` | PC → Spot | One completed sentence `text`, `token`; triggers streaming TTS. |
| `audio` | PC → Spot | Media-relative `audio`, `token`, `device`; `segment: true` for sentence clips. |
| `pause` | PC → Spot | Count of `[pause]` markers seen; marker is not shown. |
| `fallback`, `verified`, `tool_error` | PC → Spot | `token`, `device` (`fallback` also `text: "local"`; `tool_error` carries `text`). |
| `confirm` | PC → Spot | `token`, `tool`, `text`, `expires_s`, optional spoken `audio`. |
| `reply`, `error` | PC → Spot | `text`, `token`; `reply` may include `audio`, `audio_segments`, `streamed_audio`. |
| `cancelled` | both | `token`; emitted when a turn is aborted. |
| `ask` | Spot → PC | `text`. |
| `confirm` | Spot → PC | `token`, `yes`, optional `always` (persist the approval). |
| `cancel` | Spot → PC | `token` (absent = cancel all turns). |

A token identifies one reasoning round and changes after each tool result, so always answer the
token carried by the newest `confirm` event.

Voice gets one extension: `{"ch":"voice","data":{"op":"stop","assistant":true}}` routes the
transcript to Jarvis instead of dictation.

## Known limits

- Web search uses a search provider or DuckDuckGo's HTML endpoint; results are snippets, not a
  browsing agent.
- Screenshot vision needs a multimodal model: the installed `qwen3:14b` and
  `qwen2.5:7b-instruct` are text-only. `assistant_vision_model` defaults to `qwen2.5vl:7b` for
  offline Ollama, but that model must be pulled first; Gemini works when a key is configured.
- Memory is a SQLite fact store plus recent turns, with optional `nomic-embed-text` re-ranking of
  note snippets (keyword fallback). It is not a general-purpose vector database; note scanning is
  capped at 500 files of 256 KB each.
- No cloud TTS; Piper runs offline and SAPI's voice quality is whatever Windows ships.
- Undo is in-memory only and does not cover typing, timers or external services; eval dry runs
  suppress it along with other state-changing tools.
- Robot control needs the real MQTT topic/payload; it remains disabled until then.
- The APK stays uninstalled until the target rook device arrives.
